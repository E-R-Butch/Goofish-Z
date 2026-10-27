"""L2 仲裁的批次预算与条数上限回归。

生产上必须保证最坏耗时可控：条数上限 × 单次超时 与 整批预算取小。
阈值放错位置（例如共享字段）会让并发批次互相扣额度，所以这里同时验证
「计数属于引擎实例」这一点。
"""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import patch

from support import OfflineCase

from goofish_z.llm_arbiter import LLMArbiter
from goofish_z.price_value_engine import PriceValueEngine


def suspicious_item(idx: int):
    """条件 B：500 ≤ 价格 < 中位×0.78，且文本没有确定性致命标记。"""
    return {
        "item_id": f"synthetic-{idx}",
        "title": f"RTX4090 24G 自用 功能正常 {idx}",
        "price": "1000",
        "desc": "无拆无修",
    }


class _FakeArbiter:
    configured = True

    def __init__(self, verdict=None):
        self.calls = []
        self.verdict = verdict

    def judge_jev(self, item, query, baseline_price):
        self.calls.append(item.get("item_id"))
        return self.verdict


class LlmBudgetCase(OfflineCase):
    def engine(self, **env):
        defaults = {"GOOFISH_LLM_BASE_URL": "http://127.0.0.1:9/v1", "GOOFISH_LLM_API_KEY": "synthetic"}
        with patch.dict(os.environ, {**defaults, **env}):
            return PriceValueEngine(enable_llm=True, llm_model="synthetic-model")

    def test_escalations_are_capped_per_batch(self):
        engine = self.engine(GOOFISH_LLM_MAX_ESCALATIONS="3")
        fake = _FakeArbiter()
        engine.arbiter = fake
        for i in range(6):
            engine.assess(suspicious_item(i), query="4090", batch_median=12000.0)
        self.assertEqual(len(fake.calls), 3, "一批最多 3 条升级 L2")
        self.assertEqual(engine._llm_escalations, 3)

    def test_budget_exhaustion_falls_back_to_l1(self):
        engine = self.engine(GOOFISH_LLM_BATCH_BUDGET_SECONDS="10")
        fake = _FakeArbiter()
        engine.arbiter = fake

        real_monotonic = __import__("time").monotonic
        ticks = iter([100.0, 100.5, 120.0, 121.0, 122.0])  # 第 3 条时已越过 110s 截止

        def fake_monotonic():
            try:
                return next(ticks)
            except StopIteration:
                return 122.0

        with patch("goofish_z.price_value_engine.time.monotonic", side_effect=fake_monotonic):
            for i in range(4):
                engine.assess(suspicious_item(i), query="4090", batch_median=12000.0)

        self.assertLessEqual(len(fake.calls), 2, "预算耗尽后不该继续调用 L2")
        self.assertIsNotNone(engine._llm_deadline)
        self.assertGreaterEqual(real_monotonic(), 0)

    def test_counters_are_per_engine_instance(self):
        first = self.engine(GOOFISH_LLM_MAX_ESCALATIONS="1")
        second = self.engine(GOOFISH_LLM_MAX_ESCALATIONS="1")
        fake = _FakeArbiter()
        for engine in (first, second):
            engine.arbiter = fake
            engine.assess(suspicious_item(0), query="4090", batch_median=12000.0)
        self.assertEqual(len(fake.calls), 2, "两个批次各自有独立额度，不能互相扣")

    def test_invalid_env_values_fall_back_to_defaults(self):
        engine = self.engine(GOOFISH_LLM_MAX_ESCALATIONS="abc", GOOFISH_LLM_BATCH_BUDGET_SECONDS="xyz")
        self.assertEqual(engine._llm_max_escalations, 3)
        self.assertEqual(engine._llm_budget_seconds, 10.0)

    def test_transport_timeout_is_configurable(self):
        with patch.dict(os.environ, {"GOOFISH_LLM_TIMEOUT_SECONDS": "2.5"}):
            arbiter = LLMArbiter(base_url="http://127.0.0.1:9/v1", api_key="synthetic")
        self.assertEqual(arbiter.timeout, 2.5)

        with patch.dict(os.environ, {"GOOFISH_LLM_TIMEOUT_SECONDS": "not-a-number"}):
            arbiter = LLMArbiter(base_url="http://127.0.0.1:9/v1", api_key="synthetic")
        self.assertEqual(arbiter.timeout, 5.0)

    def test_unconfigured_arbiter_never_escalates(self):
        with patch.dict(os.environ, {"GOOFISH_LLM_BASE_URL": ""}, clear=False):
            engine = PriceValueEngine(enable_llm=True, llm_model="synthetic-model")
            engine.arbiter = SimpleNamespace(configured=False, judge_jev=lambda **_: self.fail("不应调用"))
            result = engine.assess(suspicious_item(0), query="4090", batch_median=12000.0)
        self.assertEqual(engine._llm_escalations, 0)
        self.assertFalse(any("JEV" in t for t in result.tags))
        asyncio.run(asyncio.sleep(0))
