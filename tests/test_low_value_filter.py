import unittest
from goofish_z.low_value_filter import LowValueClassifier


class TestLowValueClassifier(unittest.TestCase):
    def setUp(self):
        self.clf = LowValueClassifier(strict_model_match=True)

    def test_display_only(self):
        item = {"title": "CMP90HX，已解锁图形管线，仅展示。不出。", "price": "¥1558"}
        res = self.clf.evaluate(item, query="CMP 90HX")
        self.assertTrue(res.is_low_value)
        self.assertTrue(any("纯展示" in r for r in res.reasons))

    def test_unit_price_trap(self):
        item = {"title": "海盗船 DDR4 32G套装 16G×2 黑色马甲 标价是单价", "price": "¥600"}
        res = self.clf.evaluate(item, query="DDR4 32G")
        self.assertTrue(res.is_low_value)
        self.assertIn("标价为单件/单根虚假引流", " ".join(res.reasons))

    def test_hardware_defect(self):
        item = {"title": "万丽RTX3080 10G显卡 上边的dp跟下边的hdmi 不亮", "price": "¥2300"}
        res = self.clf.evaluate(item, query="RTX 3080")
        self.assertTrue(res.is_low_value)
        self.assertTrue(any("硬件缺陷" in r for r in res.reasons))

    def test_mdm_not_blocked_but_tagged(self):
        # MDM / 企业管理机正常可用且性价比高，只打标绝不拉黑
        item = {"title": "ipad mini 6 256G 企业管理机，不可刷机，升级还原已屏蔽，全功能正常", "price": "¥1499"}
        res = self.clf.evaluate(item, query="iPad mini 6")
        self.assertFalse(res.is_low_value)
        self.assertIn("MDM/企业监管", res.tags)

    def test_gpu_mismatch(self):
        item = {"title": "索泰gtx1660-6g好卡 甜甜圈正常", "price": "¥800"}
        res = self.clf.evaluate(item, query="CMP 90HX")
        self.assertTrue(res.is_low_value)
        self.assertTrue(any("型号不符" in r for r in res.reasons))

    def test_false_positive_guard_thermal_paste(self):
        # 换硅脂是正常自用保养，绝不误杀
        item = {"title": "微星 RTX3080 10G 三风扇 仅换硅脂 烤机68度正常", "price": "¥2399"}
        res = self.clf.evaluate(item, query="RTX 3080")
        self.assertFalse(res.is_low_value)

    def test_false_positive_guard_no_id_lock(self):
        # “无ID锁”必须放行，绝不误杀
        item = {"title": "iPadmini6款 64G Wi-Fi版 无ID锁，可随意升级还原", "price": "¥1600"}
        res = self.clf.evaluate(item, query="iPad mini 6")
        self.assertFalse(res.is_low_value)

    def test_multi_sku_trap(self):
        # 多 SKU 阴阳引流测试: 标题写 20G 标 ¥3299，但 20G 实际选项为 ¥4099，低配 10G 才是 ¥3299
        item = {
            "title": "RTX3080 20G 双宽涡轮公版 全新正品",
            "price": "¥3299",
            "skus": [
                {"name": "RTX3080 10G", "price": 3299.0},
                {"name": "RTX3080 20G", "price": 4099.0}
            ]
        }
        res = self.clf.evaluate(item, query="RTX 3080 20G", batch_median=3799.0)
        # 还原后的实际到手价为 4099，VMI 跌落并判定为低配引流
        self.assertTrue(res.is_low_value)
        self.assertTrue(any("多SKU引流陷阱" in r for r in res.reasons))
        self.assertIn("多SKU真实到手价:¥4099(RTX3080 20G)", res.tags)

    def test_respect_explicit_accessory_query(self):
        # 搜配件时，买家本身就是买配件，绝不误杀配件
        item = {"title": "微星 RTX4090 魔龙 原装散热器 拆机配件", "price": "¥180"}
        res = self.clf.evaluate(item, query="4090散热器")
        self.assertFalse(res.is_low_value)

    def test_marketing_negation_not_blocked(self):
        # “不卖假货”属于营销正向声明，绝不能当成“不出/仅展示”误拦截
        item = {"title": "七彩虹 RTX4090 24G 不卖假货 只出正品 箱说全", "price": "¥11500"}
        res = self.clf.evaluate(item, query="RTX 4090")
        self.assertFalse(res.is_low_value)

    def test_bundled_driver_with_hardware_not_blocked(self):
        # 真实整卡附带提供驱动，属于正常附赠服务，绝不能误判为代刷服务
        item = {"title": "RTX4090 24G显卡 功能正常 提供驱动 测试好发货", "price": "¥12000"}
        res = self.clf.evaluate(item, query="RTX 4090")
        self.assertFalse(res.is_low_value)

    def test_negated_fatal_defect_not_blocked(self):
        # 声明“无黑屏无花屏”属于完好保证，绝不能误判为致命暗病
        item = {"title": "RTX4090 24G显卡 无黑屏无花屏 功能正常 顺丰包邮", "price": "¥11800"}
        res = self.clf.evaluate(item, query="RTX 4090")
        self.assertFalse(res.is_low_value)

    def test_jev_arbiter_low_vmi_and_fallback(self):
        from goofish_z.llm_arbiter import LLMArbiter
        from unittest.mock import patch

        arbiter = LLMArbiter(api_key="mock_key")

        # 1. 状态码超出 0~4 范围时返回 None (降级回退本地规则)
        with patch("urllib.request.urlopen") as mock_url:
            mock_url.return_value.__enter__.return_value.read.return_value = b'{"choices":[{"message":{"content":"{\\"t\\":9,\\"p\\":0,\\"m\\":0}"}}]}'
            v = arbiter.judge_jev({"title": "测试", "price": "1000"}, "4090", 1000.0)
            self.assertIsNone(v)

        # 2. t=0 但真实价格过高导致 VMI < 0.65 时，必须拦截
        with patch("urllib.request.urlopen") as mock_url:
            mock_url.return_value.__enter__.return_value.read.return_value = b'{"choices":[{"message":{"content":"{\\"t\\":0,\\"p\\":4000,\\"m\\":0}"}}]}'
            v = arbiter.judge_jev({"title": "测试显卡", "price": "1000"}, "4090", 1500.0)
            self.assertIsNotNone(v)
            assert v is not None
            self.assertTrue(v.is_blocked)
            self.assertEqual(v.tier, "OVERPRICED_LOW_VALUE")

    def test_unknown_price_with_virtual_service_no_crash(self):
        from goofish_z.price_value_engine import PriceValueEngine

        pv = PriceValueEngine()
        # 面议/无标价且标题包含提供驱动，不应引发 TypeError
        item = {"title": "RTX4090 显卡 功能正常 提供驱动", "price": "面议"}
        assessment = pv.assess(item, query="RTX 4090")
        self.assertFalse(assessment.is_blocked)

    def test_jev_honors_explicit_service_and_parts_query(self):
        from goofish_z.llm_arbiter import LLMArbiter
        from unittest.mock import patch

        arbiter = LLMArbiter(api_key="mock_key")
        # 用户显式搜索“4090租赁”，返回 t=2 时不应拦截
        with patch("urllib.request.urlopen") as mock_url:
            mock_url.return_value.__enter__.return_value.read.return_value = b'{"choices":[{"message":{"content":"{\\"t\\":2,\\"p\\":0,\\"m\\":0}"}}]}'
            v = arbiter.judge_jev({"title": "RTX4090 GPU算力租赁按天出租", "price": "50"}, "4090租赁", 50.0)
            self.assertIsNotNone(v)
            assert v is not None
            self.assertFalse(v.is_blocked)

    def test_host_machine_with_4090_query_blocked(self):
        from goofish_z.price_value_engine import PriceValueEngine

        pv = PriceValueEngine()
        item = {"title": "RTX4090 海景房台式主机整机", "price": "15000"}
        assessment = pv.assess(item, query="RTX4090")
        self.assertTrue(assessment.is_blocked)
        self.assertTrue(any("整机" in r for r in assessment.reasons))


if __name__ == "__main__":
    unittest.main()
