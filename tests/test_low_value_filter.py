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

        arbiter = LLMArbiter(api_key="mock_key", base_url="http://mock-llm.invalid/v1")

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

        arbiter = LLMArbiter(api_key="mock_key", base_url="http://mock-llm.invalid/v1")
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

    def test_generic_search_does_not_block_high_end_hardware(self):
        from goofish_z.price_value_engine import PriceValueEngine

        pv = PriceValueEngine()
        # 泛搜“显卡”无统一基准时，正常万元级高档卡不应因缺少同款基准而被低VMI误杀
        item = {"title": "微星 RTX4090 超龙 24G 显卡 箱说全 功能正常", "price": "12000"}
        assessment = pv.assess(item, query="显卡", batch_median=None)
        self.assertFalse(assessment.is_blocked)


class TestPriceValueEngineReview(unittest.TestCase):
    """Codex PR#6 复审修复回归：每条对应一个曾被误杀/漏网的场景。"""

    def setUp(self):
        from goofish_z.price_value_engine import PriceValueEngine
        self.engine = PriceValueEngine()

    def _assess(self, title, price, query, batch_median=None):
        return self.engine.assess(
            {"title": title, "price": price}, query=query, batch_median=batch_median
        )

    def test_negated_id_lock_not_blocked_but_real_lock_is(self):
        ok = self._assess("iPad mini 6 64G 没有ID锁 全功能正常", "¥1800", "iPad mini 6", 1800)
        self.assertFalse(ok.is_blocked)
        locked = self._assess("iPad mini 6 64G 有ID锁 无法还原", "¥800", "iPad mini 6", 1800)
        self.assertTrue(locked.is_blocked)
        self.assertTrue(any("激活锁" in r for r in locked.reasons))

    def test_camera_listing_not_cross_category_blocked(self):
        res = self._assess("索尼 A7M4 全画幅数码相机 机身 99新", "¥9999", "索尼 A7M4")
        self.assertFalse(res.is_blocked)

    def test_non_gpu_search_not_blocked_by_accessory_rule(self):
        res = self._assess("iPhone 8 64G 包装盒齐全 功能正常", "¥200", "iPhone 8")
        self.assertFalse(res.is_blocked)

    def test_confirmed_expensive_accessory_blocked(self):
        res = self._assess("RTX4090 显卡水冷头 单卖", "¥500", "显卡")
        self.assertTrue(res.is_blocked)
        self.assertTrue(any("配件" in r for r in res.reasons))

    def test_card_mentioning_accessory_not_blocked(self):
        res = self._assess("RTX3080 24G 显卡 换好硅脂 附原装散热器 功能正常", "¥1400", "3080")
        self.assertFalse(res.is_blocked)

    def test_ai_host_machine_variants_blocked(self):
        for title in ("AI主机 RTX4090 整机出", "AI 主机 RTX4090 整机出"):
            with self.subTest(title=title):
                res = self._assess(title, "¥4000", "RTX4090")
                self.assertTrue(res.is_blocked)
                self.assertTrue(any("整机" in r for r in res.reasons))

    def test_marketing_negation_not_blocked(self):
        res = self._assess("RTX4090 出 不卖假货 只出正品 箱说全", "¥9000", "RTX4090")
        self.assertFalse(res.is_blocked)

    def test_capacity_numbers_do_not_trigger_cellular_premium(self):
        gpu = self._assess("RTX4090 24G 显卡 三风扇", "¥1700", "4090", 1000)
        self.assertNotIn("蜂窝插卡版", gpu.tags)
        ipad = self._assess("iPad mini 6 256G 4G版 全功能正常", "¥2300", "iPad mini 6", 2000)
        self.assertIn("蜂窝插卡版", ipad.tags)

    def test_shipping_qualifier_not_treated_as_non_sale(self):
        local_sale = self._assess("RTX4090 24G 显卡 不出外地，仅限同城自提", "¥9000", "RTX4090")
        self.assertFalse(local_sale.is_blocked)
        control = self._assess("CMP90HX 已解锁图形管线，仅展示。不出。", "¥1558", "90HX")
        self.assertTrue(control.is_blocked)

    def test_host_query_keeps_system_listing_mentioning_motherboard(self):
        host = self._assess("RTX4090整机 华硕主板 32G内存", "¥4000", "4090主机")
        self.assertFalse(host.is_blocked)
        plain = self._assess("RTX4090整机 华硕主板 32G内存", "¥4000", "RTX4090")
        self.assertTrue(plain.is_blocked)
        bare_board = self._assess("华硕 Z790 主板 全新未拆", "¥2000", "4090主机")
        self.assertTrue(bare_board.is_blocked)

    def test_cellular_premium_only_for_optional_cellular_products(self):
        phone = self._assess("iPhone 15 支持5G 128G 功能正常", "¥8000", "iPhone 15", 5000)
        self.assertNotIn("蜂窝插卡版", phone.tags)
        self.assertTrue(phone.is_blocked)
        tablet = self._assess("iPad mini 6 256G 4G版 全功能正常", "¥2300", "iPad mini 6", 2000)
        self.assertIn("蜂窝插卡版", tablet.tags)

    def test_parts_query_keeps_target_listings(self):
        wanted = self._assess("RTX4090 料板 无核心 供拆件", "¥200", "4090料板")
        self.assertFalse(wanted.is_blocked)
        cheap_normal = self._assess("RTX4090 料板 无核心 供拆件", "¥200", "4090")
        self.assertFalse(cheap_normal.is_blocked)  # 残值（≤base×25%）以内的练手件保持可检视
        normal = self._assess("RTX4090 料板 无核心 供拆件", "¥800", "4090")
        self.assertTrue(normal.is_blocked)

    def test_mod_adjustments_use_active_query(self):
        res = self._assess("RTX3080 20G 改4090散热 3090底板 功能正常", "¥2999", "3080 20G", 3000)
        self.assertTrue(any("4090巨型散热总成" in t for t in res.tags))
        self.assertTrue(any("3090级豪华供电PCB" in t for t in res.tags))

    def test_capacity_upgrade_rule_uses_active_query(self):
        res = self._assess("iPad mini 6 256G 国行 全功能正常", "¥2300", "iPad mini 6 64G", 1800)
        self.assertIn("256G高配", res.tags)

    def test_llm_arbiter_requires_explicit_endpoint(self):
        from goofish_z.llm_arbiter import LLMArbiter
        self.assertFalse(LLMArbiter().configured)
        self.assertFalse(LLMArbiter(api_key="mock_key").configured)
        self.assertTrue(LLMArbiter(base_url="http://mock-llm.invalid/v1", api_key="mock_key").configured)


    def test_host_query_keeps_whole_systems(self):
        kept = self._assess("RTX4090 海景房台式主机整机 游戏水冷", "¥4000", "4090主机")
        self.assertFalse(kept.is_blocked)
        blocked = self._assess("RTX4090 海景房台式主机整机 游戏水冷", "¥4000", "RTX4090")
        self.assertTrue(blocked.is_blocked)
        self.assertTrue(any("整机" in r for r in blocked.reasons))

    def test_working_bargain_not_bait_but_disclaimer_is(self):
        hint = self._assess("iPhone 15 128G 功能完好 无拆无修 正常使用", "¥1000", "iPhone 15", 3000)
        self.assertFalse(hint.is_blocked)
        self.assertTrue(any(t.startswith("观察:") for t in hint.tags))
        bait = self._assess("iPhone 15 128G 标价为定金 拍前联系", "¥1000", "iPhone 15", 3000)
        self.assertTrue(bait.is_blocked)
        self.assertTrue(any("定金" in r for r in bait.reasons))

    def test_low_price_alone_is_observation_hint(self):
        res = self._assess("RTX4090 24G 急出 功能正常", "¥30", "4090", 1000)
        self.assertFalse(res.is_blocked)
        self.assertTrue(any(t.startswith("观察:") for t in res.tags))

    def test_storage_premium_not_reapplied_for_requested_capacity(self):
        requery = self._assess("iPhone 15 512G 国行 功能正常", "¥8000", "iPhone 15 512G", 5000)
        self.assertNotIn("512G超大容量", requery.tags)
        self.assertTrue(requery.is_blocked)
        upgrade = self._assess("iPad mini 6 512G 功能正常", "¥2300", "iPad mini 6 64G", 1800)
        self.assertIn("512G超大容量", upgrade.tags)
        self.assertFalse(upgrade.is_blocked)

    def test_llm_escalation_only_for_gpu_queries(self):
        from unittest.mock import Mock
        from goofish_z.price_value_engine import PriceValueEngine
        engine = PriceValueEngine(enable_llm=True)
        fake = Mock()
        fake.configured = True
        fake.judge_jev = Mock(return_value=None)
        engine.arbiter = fake
        engine.assess({"title": "iPhone 15 128G 2000出", "price": "¥2000"}, query="iPhone 15", batch_median=3000)
        fake.judge_jev.assert_not_called()
        engine.assess({"title": "RTX4090 24G 1600 急出 功能正常", "price": "¥1600"}, query="RTX4090", batch_median=3000)
        fake.judge_jev.assert_called()


    def test_cheap_fatal_practice_card_stays_inspectable(self):
        cheap = self._assess("RTX3080 点不亮 故障练手卡", "¥100", "RTX3080", 2000)
        self.assertFalse(cheap.is_blocked)
        self.assertGreater(cheap.fair_value, 200)  # 仅按料板残值折一次，不双重折旧
        self.assertTrue(any("料板尸体" in t for t in cheap.tags))
        expensive = self._assess("RTX3080 点不亮 故障练手卡", "¥3000", "RTX3080", 2000)
        self.assertTrue(expensive.is_blocked)
        self.assertTrue(any("料板残值" in r for r in expensive.reasons))


class TestLegacyClassifierParity(unittest.TestCase):
    def setUp(self):
        self.clf = LowValueClassifier(strict_model_match=True)

    def test_negated_id_lock_phrase_not_blocked(self):
        res = self.clf.evaluate({"title": "iPad mini 6 64G 没有ID锁 全功能正常", "price": "¥1800"}, query="iPad mini 6")
        self.assertFalse(res.is_low_value)

    def test_capacity_numbers_do_not_trigger_cellular_tag(self):
        res = self.clf.evaluate({"title": "iPad mini 6 256G 国行 全功能正常", "price": "¥2300"}, query="iPad mini 6")
        self.assertNotIn("蜂窝插卡版", res.tags)

    def test_explicit_4g_marker_adds_cellular_tag(self):
        res = self.clf.evaluate({"title": "iPad mini 6 256G 4G版 全功能正常", "price": "¥2300"}, query="iPad mini 6")
        self.assertIn("蜂窝插卡版", res.tags)

    def test_round4_shipping_and_cellular_parity(self):
        local_sale = self.clf.evaluate({"title": "RTX4090 24G 显卡 不出外地，仅限同城自提", "price": "¥9000"}, query="RTX4090")
        self.assertFalse(local_sale.is_low_value)
        phone = self.clf.evaluate({"title": "iPhone 15 支持5G 128G 功能正常", "price": "¥8000"}, query="iPhone 15")
        self.assertNotIn("蜂窝插卡版", phone.tags)


if __name__ == "__main__":
    unittest.main()
