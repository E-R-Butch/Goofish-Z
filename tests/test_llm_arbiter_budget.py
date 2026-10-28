"""L2 仲裁的批次预算与条数上限回归。

生产上必须保证最坏耗时可控：条数上限 × 单次超时 与 整批预算取小。
阈值放错位置（例如共享字段）会让并发批次互相扣额度，所以这里同时验证
「计数属于引擎实例」这一点。
"""
import asyncio
import io
import os
import threading
import time
import urllib.request
from types import SimpleNamespace
from unittest.mock import patch

from support import OfflineCase

from goofish_z import llm_arbiter as arbiter_mod
from goofish_z.llm_arbiter import LLMArbiter, LLMVerdict
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

    def judge_jev(self, item, query, baseline_price, *, deadline=None):
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


class DeadlineBudgetCase(OfflineCase):
    engine = LlmBudgetCase.engine
    def test_three_four_second_requests_use_remaining_budget(self):
        engine = self.engine(GOOFISH_LLM_BATCH_BUDGET_SECONDS="10")
        clock = [100.0]
        remaining = []
        payload = b'{"choices":[{"message":{"content":"{\\"t\\":0,\\"p\\":0,\\"m\\":0}"}}]}'

        def transport(req, deadline):
            left = deadline - clock[0]
            remaining.append(left)
            clock[0] += min(4.0, left)
            if left <= 4.0:
                raise TimeoutError("synthetic deadline")
            return payload

        with patch("goofish_z.llm_arbiter.time.monotonic", side_effect=lambda: clock[0]), \
             patch("goofish_z.llm_arbiter._read_before_deadline", side_effect=transport):
            results = [engine.assess(suspicious_item(i), query="4090", batch_median=12000.0)
                       for i in range(4)]
        self.assertEqual(remaining, [5.0, 5.0, 2.0])
        self.assertEqual(clock[0], 110.0)
        self.assertTrue(all("JEV极简仲裁" in result.tags for result in results[:2]))
        self.assertTrue(all("JEV极简仲裁" not in result.tags for result in results[2:]))

    def test_late_verdict_is_discarded_even_for_custom_arbiter(self):
        engine = self.engine(GOOFISH_LLM_BATCH_BUDGET_SECONDS="10")
        clock = [100.0]

        def late_judge(**kwargs):
            self.assertEqual(kwargs["deadline"], 110.0)
            clock[0] = 111.0
            return LLMVerdict(True, 1, 12000, 12000, "GREAT_VALUE", False)

        engine.arbiter = SimpleNamespace(configured=True, judge_jev=late_judge)
        with patch("goofish_z.price_value_engine.time.monotonic", side_effect=lambda: clock[0]):
            result = engine.assess(suspicious_item(0), query="4090", batch_median=12000.0)
        self.assertNotIn("JEV极简仲裁", result.tags)
        self.assertNotEqual(result.effective_price, 1)

    def test_disabled_and_nonfinite_budget_configuration(self):
        for budget in ("0", "-1"):
            engine = self.engine(GOOFISH_LLM_BATCH_BUDGET_SECONDS=budget)
            engine.arbiter = SimpleNamespace(configured=True, judge_jev=lambda **_: self.fail("disabled"))
            engine.assess(suspicious_item(0), query="4090", batch_median=12000.0)
        for invalid in ("nan", "inf", "-inf"):
            self.assertEqual(self.engine(GOOFISH_LLM_BATCH_BUDGET_SECONDS=invalid)._llm_budget_seconds, 10.0)
        for invalid in ("nan", "inf", "-inf", "0", "-1"):
            with patch.dict(os.environ, {"GOOFISH_LLM_TIMEOUT_SECONDS": invalid}):
                self.assertEqual(LLMArbiter().timeout, 5.0)

    def test_caller_deadline_includes_slow_body_and_limits_workers(self):
        release = threading.Event()
        started = threading.Event()
        closed = threading.Event()
        slots = threading.BoundedSemaphore(1)

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                closed.set()

            def read1(self, size):
                started.set()
                release.wait(2)
                return b""

        req = urllib.request.Request("http://example.invalid")
        with patch.object(arbiter_mod, "_TRANSPORT_SLOTS", slots), \
             patch.object(urllib.request, "urlopen", return_value=Response()) as open_mock:
            try:
                start = time.monotonic()
                with self.assertRaises(TimeoutError):
                    arbiter_mod._read_before_deadline(req, start + 0.05)
                self.assertLess(time.monotonic() - start, 0.5)
                self.assertTrue(started.is_set())
                with self.assertRaisesRegex(TimeoutError, "capacity"):
                    arbiter_mod._read_before_deadline(req, time.monotonic() + 1)
                self.assertEqual(open_mock.call_count, 1)
            finally:
                release.set()
                self.assertTrue(closed.wait(2))
            self.assertTrue(slots.acquire(timeout=2))
            slots.release()

    def test_caller_deadline_includes_connection_setup(self):
        release = threading.Event()
        finished = threading.Event()
        slots = threading.BoundedSemaphore(1)

        def slow_open(req, timeout):
            try:
                release.wait(2)
                raise OSError("synthetic DNS failure")
            finally:
                finished.set()

        with patch.object(arbiter_mod, "_TRANSPORT_SLOTS", slots), \
             patch.object(urllib.request, "urlopen", side_effect=slow_open):
            try:
                start = time.monotonic()
                with self.assertRaises(TimeoutError):
                    arbiter_mod._read_before_deadline(urllib.request.Request("http://example.invalid"), start + 0.05)
                self.assertLess(time.monotonic() - start, 0.5)
            finally:
                release.set()
                self.assertTrue(finished.wait(2))
            self.assertTrue(slots.acquire(timeout=2))
            slots.release()

    def test_drip_response_stops_at_deadline_and_closes(self):
        closed = threading.Event()
        slots = threading.BoundedSemaphore(1)

        class DripResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                closed.set()

            def read1(self, size):
                time.sleep(0.01)
                return b"x"

        with patch.object(arbiter_mod, "_TRANSPORT_SLOTS", slots), \
             patch.object(urllib.request, "urlopen", return_value=DripResponse()):
            start = time.monotonic()
            with self.assertRaises(TimeoutError):
                arbiter_mod._read_before_deadline(urllib.request.Request("http://example.invalid"), start + 0.05)
            self.assertLess(time.monotonic() - start, 0.5)
            self.assertTrue(closed.wait(2))
            self.assertTrue(slots.acquire(timeout=2))
            slots.release()

    def test_budget_exhaustion_log_uses_valid_stdlib_format(self):
        engine = self.engine(GOOFISH_LLM_BATCH_BUDGET_SECONDS="10")
        with patch("goofish_z.price_value_engine.time.monotonic", side_effect=[100.0, 111.0]):
            self.assertTrue(engine._llm_slot_available())
            with self.assertLogs("goofish_z.price_value_engine", level="INFO") as logs:
                self.assertFalse(engine._llm_slot_available())
        self.assertIn("10s 已用完（已用 1 条）", logs.records[0].getMessage())

    def test_oversized_response_is_closed_and_releases_slot(self):
        slots = threading.BoundedSemaphore(1)
        response = io.BytesIO(b"x" * (arbiter_mod._MAX_RESPONSE_BYTES + 1))
        with patch.object(arbiter_mod, "_TRANSPORT_SLOTS", slots), \
             patch.object(urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "size limit"):
                arbiter_mod._read_before_deadline(urllib.request.Request("http://example.invalid"), time.monotonic() + 2)
            self.assertTrue(response.closed)
            self.assertTrue(slots.acquire(blocking=False))
            slots.release()

    def test_worker_start_failure_releases_slot(self):
        slots = threading.BoundedSemaphore(1)
        with patch.object(arbiter_mod, "_TRANSPORT_SLOTS", slots), \
             patch.object(threading.Thread, "start", side_effect=RuntimeError("synthetic unavailable thread")):
            with self.assertRaisesRegex(RuntimeError, "unavailable thread"):
                arbiter_mod._read_before_deadline(urllib.request.Request("http://example.invalid"), time.monotonic() + 2)
            self.assertTrue(slots.acquire(blocking=False))
            slots.release()
