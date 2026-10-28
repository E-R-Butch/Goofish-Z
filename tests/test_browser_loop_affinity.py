"""Loop-affinity regressions for the warm browser pool.

These lock in the bug class fixed in PR #15's follow-up: Playwright objects and
asyncio primitives belong to the loop that created them, so touching the pool
from a second event loop must fail loudly instead of leaking orphan Chrome.
"""
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

from support import OfflineCase

from goofish_z.core import browser as browser_mod


def fake_pool():
    pool = browser_mod._WarmPool()
    page = SimpleNamespace(close=AsyncMock())
    pool._ctx = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
    pool._ensure = AsyncMock()
    return pool, page


class LoopAffinityCase(OfflineCase):
    def test_pool_binds_to_first_loop_and_rejects_a_second(self):
        async def use_pool():
            pool, page = fake_pool()
            async with pool.page() as acquired:
                self.assertIs(acquired, page)
            return pool

        pool = asyncio.run(use_pool())
        owner = pool._owner_loop
        self.assertIsNotNone(owner)

        async def reuse_on_new_loop():
            async with pool.page():
                self.fail("pool accepted a second event loop")

        with self.assertRaisesRegex(RuntimeError, "跨事件循环"):
            asyncio.run(reuse_on_new_loop())
        self.assertIs(pool._owner_loop, owner)

    def test_recycle_from_foreign_loop_is_rejected(self):
        async def bind():
            pool, _ = fake_pool()
            async with pool.page():
                pass
            return pool

        pool = asyncio.run(bind())
        self.use(__import__("unittest").mock.patch.object(browser_mod, "_POOL", pool))

        async def foreign_recycle():
            await browser_mod.recycle_warm_browser("test")

        with self.assertRaisesRegex(RuntimeError, "跨事件循环"):
            asyncio.run(foreign_recycle())
        self.assertFalse(pool._lock.locked(), "foreign call must not take the pool lock")

    def test_run_in_browser_always_uses_the_worker_loop(self):
        seen = []

        async def report_loop():
            seen.append(asyncio.get_running_loop())
            return threading.get_ident()

        ident_a = asyncio.run(browser_mod.run_in_browser(report_loop))
        ident_b = asyncio.run(browser_mod.run_in_browser(report_loop))

        self.assertEqual(len(seen), 2)
        self.assertIs(seen[0], seen[1], "both requests must run on the same worker loop")
        self.assertEqual(ident_a, ident_b)
        self.assertNotEqual(ident_a, threading.get_ident())

    def test_run_in_browser_refuses_to_nest_inside_the_worker(self):
        async def nest():
            async def inner():
                return "unreachable"

            with self.assertRaisesRegex(RuntimeError, "自锁"):
                await browser_mod.run_in_browser(inner)
            return "guard-fires"

        self.assertEqual(asyncio.run(browser_mod.run_in_browser(nest)), "guard-fires")

    def test_worker_restart_discards_the_stale_pool(self):
        pool, _ = fake_pool()
        pool._owner_loop = asyncio.new_event_loop()
        self.use(__import__("unittest").mock.patch.object(browser_mod, "_POOL", pool))

        worker = browser_mod._BrowserWorker()
        loop = worker.ensure_started()
        try:
            self.assertIsNone(browser_mod._POOL, "pool bound to a dead loop must be dropped")
            asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(5)
        finally:
            worker.stop()
