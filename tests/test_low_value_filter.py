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


if __name__ == "__main__":
    unittest.main()
