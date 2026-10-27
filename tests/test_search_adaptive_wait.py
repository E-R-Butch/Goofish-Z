"""搜索页条件等待 + 常驻浏览器池的离线契约测试（不碰网络、不起浏览器）。"""

import asyncio
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from support import OfflineCase, fixture

from goofish_z.core import browser as browser_mod
from goofish_z.core.browser import _WarmPool


class FakePage:
    """最小 Page 替身：按序吐卡片数，记录所有等待动作。"""

    def __init__(self, item_counts, selector_error=None):
        self.item_counts = list(item_counts)
        self.selector_error = selector_error
        self.evaluate_calls = 0
        self.slept_ms = []
        self.selector_timeouts = []

    async def wait_for_load_state(self, *args, **kwargs):
        return None

    async def wait_for_selector(self, selector, timeout=None):
        self.selector_timeouts.append((selector, timeout))
        if self.selector_error is not None:
            raise self.selector_error
        return object()

    async def evaluate(self, script, limit):
        index = min(self.evaluate_calls, len(self.item_counts) - 1)
        count = self.item_counts[index]
        self.evaluate_calls += 1
        return {"items": [fixture(item_id=str(i)) for i in range(count)],
                "has_next": True, "page": 1}

    async def wait_for_timeout(self, ms):
        self.slept_ms.append(ms)

    async def wait_for_function(self, *args, **kwargs):
        return None


class AdaptiveWaitCase(OfflineCase):
    def read(self, page, limit=20):
        scroll = AsyncMock()
        with patch.object(self.search, "auto_scroll", scroll):
            payload = asyncio.run(self.search._read_search_page(page, limit))
        return payload, scroll, page

    def test_full_page_needs_no_scroll_and_no_blind_sleep(self):
        page = FakePage([30])
        payload, scroll, _ = self.read(page, limit=20)
        self.assertEqual(len(payload["items"]), 30)
        self.assertEqual(page.evaluate_calls, 1, "卡片已够时不应重复提取")
        self.assertEqual(scroll.await_count, 0, "卡片已够时不应滚动")
        self.assertNotIn(2000, page.slept_ms, "固定 2s 盲等必须去掉")

    def test_short_page_scrolls_once_then_reextracts(self):
        page = FakePage([5, 30])
        payload, scroll, _ = self.read(page, limit=20)
        self.assertEqual(len(payload["items"]), 30)
        self.assertEqual(page.evaluate_calls, 2)
        self.assertEqual(scroll.await_count, 1)
        self.assertEqual(scroll.await_args.kwargs.get("times"), 1)

    def test_selector_timeout_is_not_fatal(self):
        from playwright.async_api import TimeoutError as BrowserTimeout

        page = FakePage([30], selector_error=BrowserTimeout("no cards"))
        payload, scroll, _ = self.read(page, limit=20)
        self.assertEqual(len(payload["items"]), 30)
        self.assertEqual(page.selector_timeouts[0][1], self.search.CARDS_TIMEOUT_MS)
        self.assertEqual(scroll.await_count, 0)

    def test_want_capped_by_page_size(self):
        page = FakePage([30])
        _, scroll, _ = self.read(page, limit=50)
        self.assertEqual(scroll.await_count, 0, "请求 50 条时也不该因为只拿到 30 条就滚")


class BrowserWorkerCase(OfflineCase):
    def test_runs_coroutine_on_dedicated_thread(self):
        import threading as _threading

        async def peek():
            return _threading.current_thread().name

        name = asyncio.run(browser_mod.run_in_browser(peek, timeout=10))
        self.assertEqual(name, "goofish-browser")
        self.assertNotEqual(name, _threading.current_thread().name)

    def test_propagates_exception_and_keeps_thread(self):
        from goofish_z.core.errors import GoofishError

        async def boom():
            raise GoofishError("synthetic failure")

        async def peek():
            import threading as _threading
            return _threading.current_thread().name

        with self.assertRaises(GoofishError):
            asyncio.run(browser_mod.run_in_browser(boom, timeout=10))
        # 线程没被打死，后续调用仍在同一线程
        self.assertEqual(asyncio.run(browser_mod.run_in_browser(peek, timeout=10)), "goofish-browser")

    def test_serialises_two_calls(self):
        async def slow(tag):
            await asyncio.sleep(0.05)
            return tag

        async def run_both():
            return await asyncio.gather(
                browser_mod.run_in_browser(slow, "a", timeout=10),
                browser_mod.run_in_browser(slow, "b", timeout=10),
            )

        self.assertEqual(asyncio.run(run_both()), ["a", "b"])


class _FakePool(_WarmPool):
    """替换掉真实 Chrome 的部分，只验寿命/回收契约。"""

    def __init__(self):
        super().__init__()
        self.starts = 0
        self.recycled = []

    async def _start(self):
        self.starts += 1
        self._ctx = object()
        self._uses = 0
        self._born = time.monotonic()

    async def _refresh_cookies(self):
        return None

    async def recycle(self, reason=""):
        self.recycled.append(reason)
        self._ctx = None
        self._uses = 0


class WarmPoolCase(OfflineCase):
    def test_starts_lazily_and_once(self):
        pool = _FakePool()
        asyncio.run(pool._ensure())
        asyncio.run(pool._ensure())
        self.assertEqual(pool.starts, 1)
        self.assertEqual(pool.recycled, [])

    def test_recycles_at_use_limit(self):
        pool = _FakePool()
        asyncio.run(pool._ensure())
        pool._uses = browser_mod.WARM_MAX_USES
        asyncio.run(pool._ensure())
        self.assertTrue(any("复用上限" in r for r in pool.recycled))
        self.assertEqual(pool.starts, 2)

    def test_recycles_after_ttl(self):
        pool = _FakePool()
        asyncio.run(pool._ensure())
        pool._born = time.monotonic() - browser_mod.WARM_TTL_SECONDS - 1
        asyncio.run(pool._ensure())
        self.assertTrue(any("TTL" in r for r in pool.recycled))
        self.assertEqual(pool.starts, 2)

    def test_falls_back_to_per_call_profile_when_disabled(self):
        calls = []

        @asynccontextmanager
        async def fake_page():
            calls.append(True)
            yield "page"

        async def run():
            async with browser_mod.warm_page() as page:
                return page

        with patch.object(browser_mod, "WARM_REUSE", False), \
                patch.object(browser_mod, "goofish_page", fake_page):
            self.assertEqual(asyncio.run(run()), "page")
        self.assertEqual(calls, [True])
