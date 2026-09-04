import asyncio
import json
import subprocess
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from support import OfflineCase, fixture
from goofish_z.core.errors import AuthRequiredError, RateLimitedError, RiskControlError
from goofish_z.core.price import normalize_price, price_value
from goofish_z.db import WatchDB


class SearchResultsTest(OfflineCase):
    def test_price_units_and_decimal_amounts_are_converted_to_yuan(self):
        for text, expected in [("¥2.42万", 24200), ("￥3.40万", 34000),
                               ("¥9,500", 9500), ("1.25千元", 1250),
                               (" ¥ 2 . 49 万 ", 24900), ("9.99", 9.99), (0, 0)]:
            with self.subTest(text=text):
                self.assertEqual(price_value(text), expected)
        self.assertEqual(normalize_price("¥2.42万"), {
            "price": "¥24200", "price_value": 24200, "price_text": "¥2.42万",
        })

    def test_unknown_and_ambiguous_prices_do_not_become_zero_or_small_prices(self):
        for value in (None, True, "面议", "¥2.42万起", "1-2万", "-1", "NaN", "Infinity", "累计降价10%"):
            with self.subTest(value=value):
                self.assertIsNone(price_value(value))

    def test_dom_extractor_reads_unit_outside_number_wrapper(self):
        # Synthetic card models the observed sibling magnitude node, with unrelated price notes.
        script = """
        const fs = require('node:fs');
        const extract = eval('(' + fs.readFileSync(0, 'utf8') + ')');
        const text = s => ({textContent: s, getAttribute: () => s});
        const card = {
          href: 'https://example.invalid/item?id=123',
          querySelectorAll: () => [],
          querySelector: selector => {
            if (selector.includes('title')) return text('synthetic GPU');
            if (selector.includes('price-wrap')) return {
              querySelector: s => text(s.includes('number') ? '2' : '.42')
            };
            if (selector.includes('magnitude')) return text('万');
            if (selector.includes('price-desc')) return text('累计降价10%');
            return null;
          }
        };
        global.document = {
          body: {innerText: 'synthetic search'},
          querySelectorAll: () => [card],
          querySelector: selector => selector.includes('/item?id=') ? card :
            selector.includes('page-box-active') ? text('2') :
            selector.includes('arrow-right') ? {closest: () => ({disabled: false})} : null
        };
        extract(30).then(result => process.stdout.write(JSON.stringify(result)));
        """
        completed = subprocess.run(["node", "-e", script], input=self.search._EXTRACT_JS,
                                   text=True, capture_output=True, check=True)
        extracted = json.loads(completed.stdout)
        self.assertEqual(extracted["items"][0]["price"], "¥2.42万")
        self.assertEqual(extracted["page"], 2)
        self.assertTrue(extracted["has_next"])

    def test_browser_result_normalizes_price_for_all_clients(self):
        page = MagicMock()
        page.goto = page.wait_for_timeout = AsyncMock()
        page.evaluate = AsyncMock(return_value={"items": [fixture("¥2.42万")],
                                               "has_next": True, "page": 1, "source_count": 30})
        @asynccontextmanager
        async def browser():
            yield page
        with patch.object(self.search, "goofish_page", browser), \
             patch.object(self.search, "auto_scroll", AsyncMock()):
            result = asyncio.run(self.search._run("synthetic", 30))
        self.assertEqual(result["items"][0]["price"], "¥24200")
        self.assertEqual(result["items"][0]["price_value"], 24200)
        self.assertEqual(result["items"][0]["price_text"], "¥2.42万")

    def test_units_are_used_by_history_and_threshold_alerts(self):
        db = WatchDB(self.watch.DEFAULT_DB)
        wid = db.add_watch("synthetic", max_price=100)
        self.assertEqual(db.record_poll(wid, [fixture("¥2.42万")]), [])
        self.assertEqual(db.history(wid)[0]["price"], 24200)

    def test_empty_filtered_page_keeps_source_pagination(self):
        fetched = {"items": [fixture(title="synthetic DDR4 16G")], "page": 2,
                   "source_count": 1, "has_next": True}
        with patch.object(self.search, "_run", AsyncMock(return_value=fetched)) as run:
            result = self.search.search("DDR3 32G", page=2)
        run.assert_awaited_once_with("DDR3 32G", 20, 2)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["filtered_count"], 1)
        self.assertTrue(result["has_next"])
        self.assertEqual(result["page"], 2)

    def test_invalid_page_is_rejected_before_rate_limit_or_browser(self):
        for page in (0, -1, 51, True, 1.5):
            with self.subTest(page=page), self.assertRaises(ValueError):
                self.search.search("synthetic", page=page)
        self.assertFalse(self.limiter.STATE_PATH.exists())

    def test_http_page_is_validated_and_forwarded(self):
        import importlib
        api = importlib.import_module("goofish_z.api.app")
        with TestClient(api.app) as client, patch.object(api, "_call_command", return_value={}) as call:
            for value in (0, 51, "bad"):
                self.assertEqual(client.get(f"/api/search?q=synthetic&page={value}").status_code, 422)
            call.assert_not_called()
            self.assertEqual(client.get("/api/search?q=synthetic&limit=30&page=2").status_code, 200)
            call.assert_called_once_with("search.items", {"query": "synthetic", "limit": 30, "page": 2})

    def test_page_change_stops_if_guard_trips_during_wait(self):
        with patch.object(self.guard, "check", side_effect=[None, RiskControlError("synthetic")]), \
             patch.object(self.limiter, "check", side_effect=RateLimitedError("synthetic", retry_after=2)) as reserve, \
             patch.object(self.search.asyncio, "sleep", AsyncMock()):
            with self.assertRaises(RiskControlError):
                asyncio.run(self.search._wait_for_page_slot())
        self.assertEqual(reserve.call_count, 1)

    def test_page_change_does_not_click_through_login_overlay(self):
        page = MagicMock()
        page.locator.return_value.count = AsyncMock(return_value=1)
        page.locator.return_value.first.is_visible = AsyncMock(return_value=True)
        with patch.object(self.search, "_wait_for_page_slot", AsyncMock()) as reserve:
            with self.assertRaises(AuthRequiredError):
                asyncio.run(self.search._go_to_page(page, 2))
            reserve.assert_not_awaited()
