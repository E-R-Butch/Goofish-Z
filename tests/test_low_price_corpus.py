"""Synthetic rewrites of patterns observed in native ascending-price searches.

No real item IDs, seller data, links or original descriptions are retained.
Low amount alone is deliberately not a positive label for bait.
"""
from support import OfflineCase, fixture
from goofish_z.commands.search.search import filter_search_items


class LowPriceCorpusTest(OfflineCase):
    def test_observed_low_price_patterns_have_explainable_labels(self):
        cases = [
            ("合成4090远程租赁 免押金月付", "76", "服务条目"),
            ("合成RTX4090战斧散热器 无PCB", "76", "配件条目"),
            ("合成GT610显卡 1G 拆机正常", "77", "型号不匹配"),
            ("合成RTX4090尸体 PCB无核心无显存", "78", "不完整显卡"),
            ("合成4090 PCB料板 适合搬板收藏", "78", "不完整显卡"),
            ("合成4090报废卡 芯片没了，彻底报废", "78", "不完整显卡"),
            ("合成RTX4090显卡模型 核心，显存均无", "78", "不完整显卡"),
            ("合成PNY RTX4090三风扇散热器整套", "78", "配件条目"),
            ("合成4090显卡包装盒 空盒子", "79", "配件条目"),
            ("合成4090 标价为定金", "80", "非完整售价"),
        ]
        for title, price, reason in cases:
            with self.subTest(title=title):
                passed, excluded = filter_search_items("4090", [fixture(title=title, price=price)])
                self.assertEqual(passed, [])
                self.assertTrue(any(reason in r for r in excluded[0]["reasons"]))

    def test_low_price_complete_or_repairable_cards_are_not_inferred_as_bait(self):
        for title in (
            "合成RTX4090 24G 急出 功能正常", "合成4090黑屏故障卡 核心显存都在",
            "合成RTX4090显卡 原装散热器换新", "合成4090显卡 配备原装散热器",
            "合成4090显卡 无核心损伤 无显存故障", "合成4090显卡 不是无核心的模型图，实物整卡",
        ):
            with self.subTest(title=title):
                self.assertTrue(filter_search_items("4090", [fixture(title=title, price="1")])[0])

    def test_explicit_parts_queries_keep_parts_at_low_prices(self):
        for query, title in (
            ("4090散热器", "合成RTX4090散热器 无核心"),
            ("4090料板", "合成4090PCB料板 无核心无显存"),
            ("4090模型", "合成RTX4090显卡模型"),
        ):
            with self.subTest(query=query):
                self.assertTrue(filter_search_items(query, [fixture(title=title, price="1")])[0])
