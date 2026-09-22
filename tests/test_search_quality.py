from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from support import OfflineCase, fixture
from goofish_z.blacklist import capacity_matches, extract_gpu_models, is_buying_post
from goofish_z.commands.search.search import filter_search_items


class SearchQualityTest(OfflineCase):
    def classify(self, query, title):
        passed, excluded = filter_search_items(query, [fixture(title=title)])
        return passed, excluded

    def test_gpu_families_keep_suffixes_spacing_and_full_width_text(self):
        for title, expected in [
            ("合成 CMP 90 HX", {"CMP90HX"}),
            ("合成RTX90HX显卡", {"CMP90HX"}),
            ("出90hx140片", {"CMP90HX"}),
            ("合成ＧＴＸ１６６０Ｔｉ", {"GTX1660TI"}),
            ("合成 RTX-2080 Ti", {"RTX2080TI"}),
            ("合成 RX 7900 XTX", {"RX7900XTX"}),
            ("合成RX-7900-XT", {"RX7900XT"}),
            ("合成 RX580 8GB", {"RX580"}),
            ("合成 i7-12800HX 笔记本", set()),
        ]:
            with self.subTest(title=title):
                self.assertEqual(extract_gpu_models(title), expected)

    def test_wrong_gpu_is_excluded_but_comparison_to_other_gpu_is_not(self):
        for query, wrong, correct in [
            ("90HX", "合成30HX 6G显卡", "合成90HX 10G显卡 性能对标3070Ti"),
            ("GTX1660Ti", "合成GTX1660 Super显卡", "合成GTX1660 Ti显卡"),
            ("RX7900XTX", "合成RX7900 XT显卡", "合成RX7900 XTX显卡"),
        ]:
            with self.subTest(query=query):
                self.assertIn("型号不匹配", self.classify(query, wrong)[1][0]["reasons"][0])
                self.assertTrue(self.classify(query, correct)[0])

    def test_foreign_product_type_does_not_pass_gpu_search(self):
        for title in ("合成索尼 HX90 主板", "合成DSC-HX90 相机", "合成摄影镜头"):
            with self.subTest(title=title):
                self.assertIn("商品类型不符", self.classify("90HX", title)[1][0]["reasons"][0])
        for title in ("合成10G显卡型号未注明", "合成网吧倒闭台式电脑整机打包"):
            self.assertTrue(self.classify("90HX", title)[0])

    def test_comparison_gpu_does_not_count_as_the_offered_model(self):
        title = "合成90HX 10G显卡 性能对标3070Ti"
        self.assertTrue(self.classify("90HX", title)[0])
        self.assertIn("型号不匹配", self.classify("3070Ti", title)[1][0]["reasons"][0])

    def test_shared_capacity_units_work_in_both_directions(self):
        self.assertTrue(capacity_matches("合成24/48GB显卡", 24))
        self.assertTrue(capacity_matches("合成24/48GB显卡", 48))
        self.assertTrue(capacity_matches("合成４８Ｇ显卡", 48))
        self.assertTrue(self.classify("4090 24/48G", "合成4090 24GB")[0])
        self.assertFalse(self.classify("4090 48G", "合成4090 24GB")[0])

    def test_on_site_and_remote_services_are_excluded_from_product_search(self):
        for title in (
            "合成上门维修电脑", "合成同城上门清灰换硅脂", "合成上门安装显卡服务",
            "合成RTX4090 显卡维修接单", "合成90HX 代刷BIOS", "合成90HX 代装驱动",
            "合成90HX 远程安装驱动", "合成RTX4090 扩容服务", "合成RTX4090 按天租赁",
        ):
            with self.subTest(title=title):
                excluded = self.classify("90HX", title)[1]
                self.assertTrue(excluded)
                self.assertTrue(any("服务条目" in reason for reason in excluded[0]["reasons"]))

    def test_delivery_condition_and_bundled_driver_do_not_become_service_posts(self):
        for title in (
            "合成90HX显卡 功能完好无维修 支持上门自提", "合成90HX显卡 已经清灰换硅脂",
            "合成90HX显卡 成色好 送破解教程 附带驱动", "合成90HX显卡 功能正常 提供最新的解锁算力教程及驱动",
            "合成90HX显卡 单片不包邮 提供驱动技术装机 性能对标3070Ti",
            "合成90HX坏卡 维修练手 低价拆件", "合成90HX显卡维修过 已修好",
            "合成90HX显卡 支持上门安装", "合成90HX显卡 免费上门送货",
            "合成90HX显卡 不提供上门维修", "合成90HX显卡 无包装盒",
            "合成RTX4090 24G显卡 原装水冷头 功能正常",
        ):
            with self.subTest(title=title):
                self.assertTrue(self.classify("4090" if "4090" in title else "90HX", title)[0])

    def test_explicit_service_or_accessory_query_retains_relevant_offers(self):
        for query, title in (
            ("电脑上门维修", "合成电脑上门维修服务"),
            ("90HX代刷BIOS", "合成90HX代刷BIOS"),
            ("90HX驱动教程", "合成90HX驱动教程 自动发货"),
            ("4090散热器", "合成RTX4090 只卖散热器"),
        ):
            with self.subTest(query=query):
                self.assertTrue(self.classify(query, title)[0])

    def test_part_only_posts_are_excluded_but_included_parts_are_not(self):
        for title in ("合成RTX4090 只卖外壳", "合成RTX4090 仅售散热器", "合成RTX4090 包装盒单出", "合成RTX4090 水冷头不含显卡", "合成RTX4090 显卡支架", "合成RTX4090 水冷头"):
            with self.subTest(title=title):
                self.assertIn("配件条目", self.classify("4090", title)[1][0]["reasons"][0])
        for title in ("合成RTX4090显卡 带包装盒", "合成RTX4090显卡 三风扇 金属背板", "合成RTX4090显卡 散热器换新", "合成RTX4090显卡 送水冷头"):
            self.assertTrue(self.classify("4090", title)[0])

    def test_explicit_non_sale_and_incomplete_price_are_excluded(self):
        for title in (
            "合成90HX非卖贴", "合成90HX仅供展示", "合成90HX标价为定金",
            "合成90HX定金专拍", "合成90HX标价只是占位", "合成90HX非实价拍前问价",
        ):
            with self.subTest(title=title):
                self.assertTrue(self.classify("90HX", title)[1])
        for title in ("合成90HX非引流价不收定金", "合成90HX显卡低价出", "合成90HX故障显卡 低价练手"):
            self.assertTrue(self.classify("90HX", title)[0])

    def test_buying_filter_does_not_block_product_names_or_negations(self):
        for title in ("求购90HX", "诚收显卡", "高价回收电脑", "收90HX"):
            self.assertTrue(is_buying_post(title))
        for title in ("收纳盒", "收音机", "收银机", "收割机", "90HX出售不是求购", "90HX不回收旧卡", "出90HX 回收勿扰"):
            with self.subTest(title=title):
                self.assertFalse(is_buying_post(title))

    def test_http_and_monitor_search_share_exclusions_and_keep_reasons(self):
        import importlib
        api = importlib.import_module("goofish_z.api.app")
        fetched = {"items": [
            fixture(item_id="synthetic-card", title="合成90HX显卡 成色好 送教程和驱动", price="1"),
            fixture(item_id="synthetic-service", title="合成90HX上门安装服务"),
            fixture(item_id="synthetic-model", title="合成30HX显卡"),
        ], "page": 1, "source_count": 3, "has_next": True}
        with TestClient(api.app) as client, patch.object(self.search, "_run", AsyncMock(return_value=fetched)):
            response = client.get("/api/search", params={"q": "90HX"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["filtered_count"], 2)
        self.assertTrue(data["has_next"])
        self.assertTrue(all(row["title"] and row["url"] and row["reasons"] for row in data["filtered"]))
        with patch.object(self.search, "_run", AsyncMock(return_value=fetched)), patch("goofish_z.core.limiter.check"):
            monitor = self.search.search("90HX", filter_blacklist=False)
        self.assertEqual(monitor["filtered"], data["filtered"])
        self.assertEqual(monitor["items"][0]["item_id"], "synthetic-card")
