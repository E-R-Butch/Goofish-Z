from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from support import OfflineCase, fixture
from goofish_z.blacklist import BlacklistDB, _extract_capacity, capacity_matches, extract_gpu_models


class SearchMatchingTest(OfflineCase):
    def test_chinese_capacity_boundary_retains_target_in_multi_capacity_titles(self):
        for title in ("合成4090 24G 48G涡轮", "合成4090-24/48G显卡", "合成显存48GB全新", "48g涡轮版"):
            with self.subTest(title=title):
                self.assertTrue(capacity_matches(title, 48))
        self.assertEqual(_extract_capacity("48G涡轮"), 48)
        self.assertEqual(_extract_capacity("1024GB存储"), 1024)
        self.assertFalse(capacity_matches("合成4090 24G显卡", 48))
        self.assertTrue(capacity_matches("合成显卡容量未注明", 48))

    def test_frequency_and_partial_numbers_are_not_capacity(self):
        for title in ("合成48GHz", "合成1.5G", "合成48Gbps", "合成14800G"):
            with self.subTest(title=title):
                self.assertIsNone(_extract_capacity(title))

    def test_gpu_suffix_is_part_of_model_and_not_a_substring_match(self):
        for title, expected in (
            ("全新4090D-48G涡轮", {"RTX4090D"}),
            ("RTX 4090 d 显卡", {"RTX4090D"}),
            ("4090显卡 DDR6X", {"RTX4090"}),
            ("4090-24/48G 4090d-24/48G涡轮", {"RTX4090", "RTX4090D"}),
            ("RTX4070Ti SUPER显卡", {"RTX4070TISUPER"}),
            ("合成编号140900", set()),
        ):
            with self.subTest(title=title):
                self.assertEqual(extract_gpu_models(title), expected)

    def test_capacity_model_and_confirmed_price_reason_stay_distinct(self):
        db = BlacklistDB(self.root / "watch.db")
        db.add_rule("item_id", "synthetic-bait", "非实价／引流价（人工确认）")
        fetched = {"items": [
            fixture(item_id="synthetic-multi", title="合成4090 24G 48G涡轮"),
            fixture(item_id="synthetic-bait", title="合成4090-24/48G 4090D-24/48G涡轮"),
            fixture(item_id="synthetic-wrong-capacity", title="合成4090 24G显卡"),
            fixture(item_id="synthetic-wrong-model", title="合成4090D-48G涡轮"),
            fixture("1", item_id="synthetic-low-price", title="合成4090 48G显卡"),
        ], "page": 1, "source_count": 5, "has_next": True}
        with patch.object(self.search, "_run", AsyncMock(return_value=fetched)):
            result = self.search.search("4090 48G")
        self.assertEqual({it["item_id"] for it in result["items"]}, {"synthetic-multi", "synthetic-low-price"})
        self.assertEqual(result["filtered_count"], 2)
        details = {it["item_id"]: it for it in result["filtered"]}
        self.assertIn("容量不匹配", details["synthetic-wrong-capacity"]["reasons"][0])
        self.assertIn("型号不匹配", details["synthetic-wrong-model"]["reasons"][0])
        self.assertEqual(result["blocked_count"], 1)
        self.assertEqual(result["blocked"][0]["item_id"], "synthetic-bait")
        self.assertEqual(result["blocked"][0]["reasons"], ["非实价／引流价（人工确认）"])
        self.assertTrue(result["has_next"])

    def test_d_query_excludes_base_model_but_retains_mixed_and_unspecified_titles(self):
        fetched = {"items": [
            fixture(item_id="synthetic-base", title="合成RTX4090 48G"),
            fixture(item_id="synthetic-d", title="合成RTX4090D 48G"),
            fixture(item_id="synthetic-mixed", title="合成RTX4090/4090D 48G"),
            fixture(item_id="synthetic-unknown", title="合成48G显卡"),
        ], "page": 1, "source_count": 4, "has_next": False}
        with patch.object(self.search, "_run", AsyncMock(return_value=fetched)):
            result = self.search.search("4090D 48G")
        self.assertEqual({it["item_id"] for it in result["items"]}, {"synthetic-d", "synthetic-mixed", "synthetic-unknown"})
        self.assertEqual(result["filtered"][0]["item_id"], "synthetic-base")

    def test_item_rule_matches_only_exact_item_and_can_be_disabled(self):
        db = BlacklistDB(self.root / "watch.db")
        rid = db.add_rule("item_id", "synthetic-one", "人工确认原因")
        items = [fixture(item_id="synthetic-one", seller_nick="synthetic seller"),
                 fixture(item_id="synthetic-one-more", seller_nick="synthetic seller")]
        passed, blocked = db.filter_items(items)
        self.assertEqual(len(passed), 1)
        self.assertEqual(blocked[0]["_blocked_reasons"], ["人工确认原因"])
        db.set_enabled(rid, False)
        self.assertEqual(len(db.filter_items(items)[0]), 2)

    def test_item_rule_is_available_through_http(self):
        import importlib
        api = importlib.import_module("goofish_z.api.app")
        with TestClient(api.app) as client:
            response = client.post("/api/blacklist/add", json={
                "kind": "item_id", "value": "synthetic-item", "note": "非实价／引流价（人工确认）",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["added"]["kind"], "item_id")
        db = BlacklistDB(self.root / "watch.db")
        self.assertEqual(db.filter_items([fixture(item_id="synthetic-item")])[1][0]["_blocked_reasons"],
                         ["非实价／引流价（人工确认）"])
