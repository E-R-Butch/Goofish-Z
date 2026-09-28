import asyncio
import json
import subprocess
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from support import OfflineCase, fixture
from goofish_z.core.errors import AuthRequiredError, GoofishError, RateLimitedError, RiskControlError
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
        global.window = {location: {href: 'https://www.goofish.com/search?q=synthetic'}};
        extract(30).then(result => process.stdout.write(JSON.stringify(result)));
        """
        completed = subprocess.run(["node", "-e", script], input=self.search._EXTRACT_JS,
                                   text=True, capture_output=True, check=True)
        extracted = json.loads(completed.stdout)
        self.assertEqual(extracted["items"][0]["price"], "¥2.42万")
        self.assertEqual(extracted["page"], 2)
        self.assertTrue(extracted["has_next"])
        self.assertEqual(extracted["source_query"], "synthetic")

    def test_browser_result_normalizes_price_for_all_clients(self):
        page = MagicMock()
        page.goto = page.wait_for_timeout = page.wait_for_load_state = AsyncMock()
        page.evaluate = AsyncMock(return_value={"items": [fixture("¥2.42万")],
                                               "source_query": "synthetic", "has_next": True, "page": 1, "source_count": 30})
        @asynccontextmanager
        async def browser():
            yield page
        with patch.object(self.search, "goofish_page", browser), \
             patch.object(self.search, "auto_scroll", AsyncMock()):
            result = asyncio.run(self.search._run("synthetic", 30))
        self.assertEqual(result["items"][0]["price"], "¥24200")
        self.assertEqual(result["items"][0]["price_value"], 24200)
        self.assertEqual(result["items"][0]["price_text"], "¥2.42万")
        self.assertEqual(result["query"], "synthetic")

    def test_scroll_tolerates_body_disappearing_after_navigation_wait(self):
        from goofish_z.core.browser import auto_scroll
        page = MagicMock()
        page.wait_for_function = AsyncMock()
        page.evaluate = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        asyncio.run(auto_scroll(page, times=1, pause_ms=0))
        scroll = page.evaluate.await_args_list[0].args[0]
        # The DOM can be replaced immediately after the wait succeeds.
        script = """
        const fs = require('node:fs');
        const scroll = eval('(' + fs.readFileSync(0, 'utf8') + ')');
        global.document = {body: null};
        const calls = [];
        global.window = {scrollTo: (...args) => calls.push(args)};
        scroll();
        document.body = {scrollHeight: 300};
        scroll();
        process.stdout.write(JSON.stringify(calls));
        """
        completed = subprocess.run(['node','-e',script],input=scroll,text=True,capture_output=True,check=True)
        self.assertEqual(json.loads(completed.stdout), [[0, 300]])

    def test_source_keyword_mismatch_and_missing_provenance_are_rejected(self):
        for source in ('4090', ''):
            with self.subTest(source=source), self.assertRaisesRegex(GoofishError, '关键词.*不一致'):
                self.search._check_payload({'items':[fixture()], 'source_query':source}, '4080S 32G')

    def test_repeated_navigation_failure_is_bounded_and_explained(self):
        from playwright.async_api import Error as BrowserError
        page = MagicMock()
        page.wait_for_load_state = page.wait_for_timeout = AsyncMock()
        page.evaluate = AsyncMock(side_effect=BrowserError('Execution context was destroyed'))
        with patch.object(self.search, 'auto_scroll', AsyncMock()), self.assertRaisesRegex(GoofishError, '仍在跳转'):
            asyncio.run(self.search._read_search_page(page, 30))
        self.assertEqual(page.evaluate.await_count, 2)
        page.goto.assert_not_called()

    def test_auth_redirect_retries_dom_read_once_without_another_navigation(self):
        from playwright.async_api import Error as BrowserError
        page = MagicMock()
        page.wait_for_timeout = page.wait_for_load_state = AsyncMock()
        page.evaluate = AsyncMock(side_effect=[BrowserError("Execution context was destroyed"), {"items": [fixture()]}])
        with patch.object(self.search, "auto_scroll", AsyncMock()):
            result = asyncio.run(self.search._read_search_page(page, 30))
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(page.evaluate.await_count, 2)
        page.goto.assert_not_called()

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

    def test_each_automatic_filter_retains_the_item_and_its_reason(self):
        fetched = {"items": [
            fixture(item_id="synthetic-noise", title="求购 DDR3 32G"),
            fixture(item_id="synthetic-capacity", title="合成 DDR3 16G"),
            fixture(item_id="synthetic-generation", title="合成 DDR4 32G"),
            fixture(item_id="synthetic-passed", title="合成 DDR3 32G"),
        ], "page": 1, "source_count": 4, "has_next": True}
        with patch.object(self.search, "_run", AsyncMock(return_value=fetched)):
            result = self.search.search("DDR3 32G")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["filtered_count"], 3)
        details = {item["item_id"]: item for item in result["filtered"]}
        self.assertEqual(details["synthetic-noise"]["reasons"], ["收购帖"])
        self.assertIn("容量不匹配", details["synthetic-capacity"]["reasons"][0])
        self.assertIn("DDR4", details["synthetic-generation"]["reasons"][0])
        self.assertTrue(all(item["url"] and item["price"] and item["title"] for item in details.values()))

    def test_blacklist_details_keep_full_title_link_and_rule_reason(self):
        title = "合成被屏蔽商品的完整说明" * 10
        item = fixture(title=title, _blocked_reasons=["合成屏蔽规则"], raw={"private": "synthetic"})
        fetched = {"items": [item], "page": 1, "source_count": 1, "has_next": True}
        with patch.object(self.search, "_run", AsyncMock(return_value=fetched)), \
             patch("goofish_z.blacklist.BlacklistDB.filter_items", return_value=([], [item])):
            result = self.search.search("synthetic")
        detail = result["blocked"][0]
        self.assertEqual(result["blocked_count"], 1)
        self.assertEqual(detail["title"], title)
        self.assertEqual(detail["url"], item["url"])
        self.assertEqual(detail["reasons"], ["合成屏蔽规则"])
        self.assertNotIn("raw", detail)

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

    def test_impossible_price_range_is_rejected_before_search(self):
        with self.assertRaises(ValueError):
            self.search.search("synthetic", min_price=100, max_price=50)
        self.assertFalse(self.limiter.STATE_PATH.exists())

    def test_price_fence_excludes_unknown_prices(self):
        fetched = {"items": [
            fixture("600", item_id="synthetic-known", title="合成iPhone 15 功能正常"),
            fixture("面议", item_id="synthetic-negotiable", title="合成iPhone 15 面议"),
        ], "page": 1, "source_count": 2, "has_next": False}
        with patch.object(self.search, "_run", AsyncMock(return_value=fetched)):
            result = self.search.search("iPhone 15", min_price=500, max_price=2000)
        self.assertEqual([it["item_id"] for it in result["items"]], ["synthetic-known"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["blocked_count"], 1)
        self.assertEqual(result["blocked"][0]["item_id"], "synthetic-negotiable")
        self.assertIn("价格未知", result["blocked"][0]["reasons"][0])

    def test_short_product_models_count_as_specific_queries(self):
        for value in ("iPhone 15", "iPad mini 6", "小米14", "RTX4090", "90HX", "DDR4 32G"):
            with self.subTest(value=value):
                self.assertTrue(self.search._mentions_specific_product(value))
        for value in ("显卡", "投影仪", "电脑主机"):
            with self.subTest(value=value):
                self.assertFalse(self.search._mentions_specific_product(value))

    def test_short_model_query_enables_price_value_filtering(self):
        fetched = {"items": [
            fixture("1500", item_id="synthetic-normal-a", title="合成iPhone 15 功能正常"),
            fixture("1500", item_id="synthetic-normal-b", title="合成iPhone 15 功能正常"),
            fixture("6000", item_id="synthetic-overpriced", title="合成iPhone 15 指纹坏 功能正常"),
        ], "page": 1, "source_count": 3, "has_next": False}
        with patch.object(self.search, "_run", AsyncMock(return_value=fetched)):
            result = self.search.search("iPhone 15")
        self.assertEqual({it["item_id"] for it in result["items"]},
                         {"synthetic-normal-a", "synthetic-normal-b"})
        detail = next(it for it in result["filtered"] if it["item_id"] == "synthetic-overpriced")
        self.assertTrue(any("价格与价值严重不匹配" in reason for reason in detail["reasons"]))

    def test_median_ignores_cheap_accessories_and_phones_survive(self):
        fetched = {"items": [
            fixture("15", item_id="synthetic-case", title="合成iPhone 15 手机壳 全新未拆"),
            fixture("9", item_id="synthetic-film", title="合成iPhone 15 钢化膜 2片装"),
            fixture("25", item_id="synthetic-cover", title="合成iPhone 15 保护套 硅胶"),
            fixture("29", item_id="synthetic-stand", title="合成iPhone 15 手机支架 桌面"),
            fixture("45", item_id="synthetic-cable", title="合成iPhone 15 数据线 快充线"),
            fixture("2600", item_id="synthetic-phone-a", title="合成iPhone 15 128G 功能正常"),
            fixture("2800", item_id="synthetic-phone-b", title="合成iPhone 15 128G 国行 电池91"),
            fixture("3000", item_id="synthetic-phone-c", title="合成iPhone 15 128G 成色新 无拆修磕碰"),
        ], "page": 1, "source_count": 8, "has_next": False}
        with patch.object(self.search, "_run", AsyncMock(return_value=fetched)):
            result = self.search.search("iPhone 15")
        kept = {it["item_id"] for it in result["items"]}
        for phone in ("synthetic-phone-a", "synthetic-phone-b", "synthetic-phone-c"):
            self.assertIn(phone, kept)
        vmi = next(it["vmi"] for it in result["items"] if it["item_id"] == "synthetic-phone-a")
        self.assertGreater(vmi, 0.9)
        self.assertEqual(result["blocked_count"], 0)

    def test_variant_query_excludes_other_variants_from_median(self):
        fetched = {"items": [
            fixture("3000", item_id="synthetic-base-a", title="合成iPhone 15 128G 功能正常"),
            fixture("3000", item_id="synthetic-base-b", title="合成iPhone 15 128G 国行 电池91"),
            fixture("3100", item_id="synthetic-base-c", title="合成iPhone 15 128G 成色新"),
            fixture("6000", item_id="synthetic-pro-a", title="合成iPhone 15 Pro 256G 功能正常"),
            fixture("6200", item_id="synthetic-pro-b", title="合成iPhone 15 Pro 256G 国行"),
        ], "page": 1, "source_count": 5, "has_next": False}
        with patch.object(self.search, "_run", AsyncMock(return_value=fetched)):
            result = self.search.search("iPhone 15 Pro")
        kept = {it["item_id"] for it in result["items"]}
        self.assertIn("synthetic-pro-a", kept)
        self.assertIn("synthetic-pro-b", kept)
        self.assertIn("synthetic-base-a", kept)
