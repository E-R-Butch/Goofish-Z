"""搜索结果的规格补全（--resolve-skus）。

回归目标：search 曾读 details["skuList"]，而 get_item 顶层键是 skus，
条件恒为假 —— 规格补全从未生效，且异常被光秃秃的 except: pass 吞掉。
"""

import importlib
from unittest.mock import AsyncMock, patch

from support import OfflineCase

SKUS = [{"name": "32G", "price": "1800", "stock": "1"}]


class SkuResolutionTest(OfflineCase):
    def setUp(self):
        super().setUp()
        self.search_mod = importlib.import_module("goofish_z.commands.search.search")
        self.item_get = importlib.import_module("goofish_z.commands.item.get")

    def test_skus_are_attached_from_get_item(self):
        item = {"item_id": "synthetic-item-1", "title": "多规格显卡", "price": "¥1000"}
        with patch.object(self.item_get, "_get", return_value={"skus": SKUS}):
            self.search_mod._attach_item_skus(item)
        self.assertEqual(item["skus"], SKUS)

    def test_legacy_skulist_key_is_still_accepted(self):
        item = {"item_id": "synthetic-item-2", "title": "多规格显卡", "price": "¥1000"}
        with patch.object(self.item_get, "_get", return_value={"skuList": SKUS}):
            self.search_mod._attach_item_skus(item)
        self.assertEqual(item["skus"], SKUS)

    def test_detail_failure_keeps_item_untouched(self):
        item = {"item_id": "synthetic-item-3", "title": "多规格显卡", "price": "¥1000"}
        before = dict(item)
        with patch.object(self.item_get, "_get", side_effect=RuntimeError("rate limited")):
            self.search_mod._attach_item_skus(item)
        self.assertEqual(item, before)
        self.assertNotIn("skus", item)

    def test_item_without_id_is_skipped(self):
        item = {"title": "无 id", "price": "¥1000"}
        with patch.object(self.item_get, "_get", side_effect=AssertionError("should not fetch")):
            self.search_mod._attach_item_skus(item)
        self.assertNotIn("skus", item)

    def test_empty_skus_are_not_attached(self):
        item = {"item_id": "synthetic-item-4", "title": "多规格显卡", "price": "¥1000"}
        with patch.object(self.item_get, "_get", return_value={"skus": []}):
            self.search_mod._attach_item_skus(item)
        self.assertNotIn("skus", item)

    def test_search_entry_attaches_skus(self):
        """从 search() 入口验证：把调用点改回内联 skuList 判断，这条会失败。"""
        item = {"item_id": "synthetic-item-9", "title": "多规格 显卡", "price": "¥1000"}
        payload = {"items": [item], "source_query": "多规格 显卡", "page": 1}
        with (
            patch.object(self.search_mod, "_run", new=AsyncMock(return_value=payload)),
            patch.object(self.search_mod, "filter_search_items", return_value=([item], [])),
            patch("goofish_z.core.limiter.check"),
            patch("goofish_z.core.guard.check"),
            patch.object(self.item_get, "_get", return_value={"skus": SKUS}),
        ):
            result = self.search_mod.search(
                "多规格 显卡", resolve_skus=True, filter_blacklist=False
            )
        self.assertEqual(result["items"][0]["skus"], SKUS)
