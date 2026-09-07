import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from support import OfflineCase
from goofish_z.prices.browser import CollectionError, collect_page, market_page, profile_root


class MarketBrowserTest(OfflineCase):
    def page(self, payload, status=200):
        page = MagicMock()
        page.goto = AsyncMock(return_value=MagicMock(status=status))
        page.evaluate = AsyncMock(return_value=payload)
        page.wait_for_timeout = AsyncMock()
        page.wait_for_load_state = AsyncMock()
        @asynccontextmanager
        async def browser(*args, **kwargs):
            yield page
        self.use(patch("goofish_z.prices.browser.market_page", browser))
        return page

    def test_http_failure_is_not_reported_as_a_parser_problem(self):
        page = self.page({}, status=403)
        with self.assertRaises(CollectionError) as error:
            asyncio.run(collect_page("jd", "synthetic", 10))
        self.assertEqual(error.exception.status, "access_failed")
        self.assertIn("HTTP 403", str(error.exception))
        page.evaluate.assert_not_awaited()

    def test_login_form_without_product_nodes_is_distinguished_from_broken_selectors(self):
        self.page({"items": [], "verification": False, "login": False, "empty": False,
                   "diagnostics": {"login_signals": ["扫码登录"], "visible_product_nodes": 0}})
        with self.assertRaises(CollectionError) as error:
            asyncio.run(collect_page("jd", "synthetic", 10))
        self.assertEqual(error.exception.status, "login_required")
        self.assertEqual(error.exception.diagnostics["visible_product_nodes"], 0)

    def test_header_login_link_alone_does_not_prove_login_is_required(self):
        self.page({"items": [], "verification": False, "login": False, "empty": False,
                   "diagnostics": {"login_signals": ["请登录"], "visible_product_nodes": 0}})
        with self.assertRaises(CollectionError) as error:
            asyncio.run(collect_page("jd", "synthetic", 10))
        self.assertEqual(error.exception.status, "parse_failed")

    def test_failed_cookie_import_is_not_marked_applied(self):
        root = profile_root("jd")
        target = root / "imported-cookies.json"
        target.write_text(json.dumps([{"domain": ".unrelated.invalid", "name": "synthetic", "value": "synthetic"}]))
        browser = MagicMock()
        browser.add_cookies = AsyncMock()
        browser.new_page = AsyncMock()
        browser.close = AsyncMock()
        pw = MagicMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=browser)
        @asynccontextmanager
        async def playwright():
            yield pw
        self.use(patch("playwright.async_api.async_playwright", playwright))
        async def attempt():
            async with market_page("jd", reserve=False):
                self.fail("invalid credentials must never reach a page")
        with self.assertRaises(CollectionError):
            asyncio.run(attempt())
        self.assertFalse((root / "cookies-applied").exists())
        browser.add_cookies.assert_not_awaited()
        browser.close.assert_awaited_once()

    def test_login_profile_without_import_does_not_access_chrome_cookies(self):
        browser = MagicMock()
        page = MagicMock()
        page.route = AsyncMock()
        browser.add_cookies = AsyncMock()
        browser.new_page = AsyncMock(return_value=page)
        browser.close = AsyncMock()
        pw = MagicMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=browser)
        @asynccontextmanager
        async def playwright():
            yield pw
        self.use(patch("playwright.async_api.async_playwright", playwright))
        async def attempt():
            async with market_page("jd", reserve=False) as opened:
                self.assertIs(opened, page)
        asyncio.run(attempt())
        browser.add_cookies.assert_not_awaited()
        browser.close.assert_awaited_once()
