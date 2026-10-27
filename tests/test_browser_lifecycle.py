"""Offline regression tests for the warm browser ownership boundary."""
import asyncio
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from support import OfflineCase
from goofish_z.core import browser as browser_mod


class BrowserLifecycleCase(OfflineCase):
    def pool(self):
        pool = browser_mod._WarmPool()
        page = SimpleNamespace(close=AsyncMock())
        pool._ctx = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
        pool._ensure = AsyncMock()
        return pool, page

    def test_failed_request_recycles_on_owner_loop_before_unlock(self):
        async def run():
            pool, page = self.pool()
            ctx = pool._ctx
            owner = asyncio.get_running_loop()
            async def close():
                self.assertIs(asyncio.get_running_loop(), owner)
                self.assertTrue(pool._lock.locked())
            ctx.close.side_effect = close
            with self.assertRaisesRegex(ValueError, "synthetic"):
                async with pool.page():
                    raise ValueError("synthetic")
            ctx.close.assert_awaited_once()
            page.close.assert_not_awaited()
            self.assertFalse(pool.alive)
        asyncio.run(run())

    def test_cancelled_waiter_does_not_close_active_context(self):
        async def run():
            pool, _ = self.pool()
            ctx = pool._ctx
            async with pool.page():
                async def waiter():
                    async with pool.page():
                        self.fail("waiter acquired active pool")
                task = asyncio.create_task(waiter())
                await asyncio.sleep(0)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                ctx.close.assert_not_awaited()
                self.assertTrue(pool.alive)
        asyncio.run(run())

    def test_cancelled_active_request_recycles(self):
        async def run():
            pool, _ = self.pool()
            ctx = pool._ctx
            entered = asyncio.Event()
            async def active():
                async with pool.page():
                    entered.set()
                    await asyncio.Event().wait()
            task = asyncio.create_task(active())
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            ctx.close.assert_awaited_once()
            self.assertFalse(pool._lock.locked())
        asyncio.run(run())

    def test_changed_cookies_recreate_context_and_storage(self):
        async def run():
            pool, _ = self.pool()
            ctx = pool._ctx
            pool._cookie_snapshot = [{"name": "old", "value": "synthetic-old"}]
            pool._start = AsyncMock()
            with patch.object(browser_mod, "_load_cookies_from_session", return_value={"new": "synthetic-new"}):
                await pool._refresh_cookies()
            ctx.close.assert_awaited_once()
            pool._start.assert_awaited_once()
        asyncio.run(run())

    def test_unchanged_cookies_replace_browser_cookie_jar(self):
        async def run():
            pool, _ = self.pool()
            pool._ctx.clear_cookies = AsyncMock()
            pool._ctx.add_cookies = AsyncMock()
            cookies = {"synthetic": "value"}
            pool._cookie_snapshot = browser_mod._cookies_to_playwright(cookies)
            with patch.object(browser_mod, "_load_cookies_from_session", return_value=cookies):
                await pool._refresh_cookies()
            pool._ctx.clear_cookies.assert_awaited_once()
            pool._ctx.add_cookies.assert_awaited_once_with(pool._cookie_snapshot)
        asyncio.run(run())

    def test_partial_start_failure_or_cancellation_cleans_resources(self):
        for error in (RuntimeError("script failed"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as root:
                async def run():
                    ctx = SimpleNamespace(add_init_script=AsyncMock(side_effect=error), close=AsyncMock())
                    pw = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=AsyncMock(return_value=ctx)), stop=AsyncMock())
                    manager = SimpleNamespace(start=AsyncMock(return_value=pw))
                    pool = browser_mod._WarmPool()
                    with patch("playwright.async_api.async_playwright", return_value=manager), patch.object(browser_mod, "PROFILES_PARENT", Path(root)):
                        with self.assertRaises(type(error)):
                            await pool._start()
                    ctx.close.assert_awaited_once()
                    pw.stop.assert_awaited_once()
                    self.assertEqual(list(Path(root).iterdir()), [])
                    self.assertFalse(pool.alive)
                asyncio.run(run())

    def test_public_recycle_waits_for_active_request(self):
        async def run():
            pool, _ = self.pool()
            ctx = pool._ctx
            with patch.object(browser_mod, "_POOL", pool):
                async with pool.page():
                    recycle = asyncio.create_task(browser_mod.recycle_warm_browser("test"))
                    await asyncio.sleep(0)
                    ctx.close.assert_not_awaited()
                await recycle
                ctx.close.assert_awaited_once()
        asyncio.run(run())

    def test_concurrent_worker_start_waits_for_one_loop(self):
        worker = browser_mod._BrowserWorker()
        release = threading.Event()
        entered = threading.Event()
        original = worker._run_forever
        def delayed():
            entered.set()
            release.wait(5)
            original()
        worker._run_forever = delayed
        loops, errors = [], []
        def start():
            try:
                loops.append(worker.ensure_started())
            except Exception as exc:
                errors.append(exc)
        first = threading.Thread(target=start)
        second = threading.Thread(target=start)
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        release.set()
        first.join(3)
        second.join(3)
        try:
            self.assertEqual(errors, [])
            self.assertEqual(len(loops), 2)
            self.assertIsNotNone(loops[0])
            self.assertIs(loops[0], loops[1])
        finally:
            worker.stop()
            worker._thread.join(3)

    def test_cancellation_during_recycle_finishes_detached_cleanup(self):
        async def run():
            pool, _ = self.pool()
            ctx = pool._ctx
            started, finish = asyncio.Event(), asyncio.Event()
            async def close():
                started.set()
                await finish.wait()
            ctx.close.side_effect = close
            pw = SimpleNamespace(stop=AsyncMock())
            pool._pw = pw
            task = asyncio.create_task(pool.recycle("test"))
            await started.wait()
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            pw.stop.assert_awaited_once()
        asyncio.run(run())

    def test_cancellation_during_page_close_recycles_context(self):
        async def run():
            pool, page = self.pool()
            ctx = pool._ctx
            closing = asyncio.Event()
            async def close():
                closing.set()
                await asyncio.Event().wait()
            page.close.side_effect = close
            async def request():
                async with pool.page():
                    pass
            task = asyncio.create_task(request())
            await closing.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            ctx.close.assert_awaited_once()
            self.assertFalse(pool.alive)
        asyncio.run(run())

    def test_worker_drain_cancels_request_before_recycling(self):
        worker = browser_mod._BrowserWorker()
        loop = worker.ensure_started()
        entered = threading.Event()
        async def setup():
            pool, _ = self.pool()
            ctx = pool._ctx
            async def active():
                async with pool.page():
                    entered.set()
                    await asyncio.Event().wait()
            task = asyncio.create_task(active())
            browser_mod._BROWSER_REQUESTS.add(task)
            task.add_done_callback(browser_mod._BROWSER_REQUESTS.discard)
            return pool, ctx, task
        pool, ctx, task = asyncio.run_coroutine_threadsafe(setup(), loop).result(3)
        self.assertTrue(entered.wait(2))
        try:
            with patch.object(browser_mod, "_POOL", pool):
                asyncio.run_coroutine_threadsafe(browser_mod._drain_browser_worker(), loop).result(3)
            self.assertTrue(task.cancelled())
            ctx.close.assert_awaited_once()
            self.assertFalse(pool.alive)
        finally:
            worker.stop()
            worker._thread.join(3)
