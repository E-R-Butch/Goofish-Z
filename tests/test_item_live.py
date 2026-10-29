"""在售校验的离线契约：只看标题判活、串行复用页面、单条失败不拖累整批。

不启动浏览器：`warm_page` 用假页面替换；合成 ID 用几位纯数字，避开公开仓库的
长数字门禁。
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from goofish_z.commands.item import live


class JudgeTest(unittest.TestCase):
    def test_generic_title_is_offline(self) -> None:
        self.assertEqual(live.judge_item_page({"title": live.GENERIC_TITLE}), "offline")

    def test_item_title_is_live(self) -> None:
        self.assertEqual(live.judge_item_page({"title": "3080 20G 魔改_闲鱼"}), "live")

    def test_empty_or_missing_title_is_offline(self) -> None:
        for payload in ({}, {"title": ""}, {"title": "   "}, {"title": None}):
            self.assertEqual(live.judge_item_page(payload), "offline", msg=repr(payload))

    def test_verification_page_with_its_own_title_is_unknown(self) -> None:
        # 登录/验证页标题既不为空也不是站点通用标题，不能当在售。
        for payload in (
            {"title": "安全验证_闲鱼"},
            {"title": "闲鱼 - 请登录"},
            {"title": "商品_闲鱼", "head": "请完成验证码"},
        ):
            self.assertEqual(live.judge_item_page(payload), "unknown", msg=repr(payload))

    def test_non_product_page_title_is_unknown(self) -> None:
        # 目录页/跳转页：标题存在但不是「宝贝标题_闲鱼」形状。
        self.assertEqual(live.judge_item_page({"title": "闲鱼"}), "unknown")

    def test_body_length_does_not_decide(self) -> None:
        # 早期实现按 body 长度判，SPA 水合慢会误杀；长度已不参与判决。
        self.assertEqual(
            live.judge_item_page({"title": "在售商品_闲鱼", "len": 10}), "live"
        )
        self.assertEqual(
            live.judge_item_page({"title": live.GENERIC_TITLE, "len": 9000}), "offline"
        )


class CheckItemsLiveTest(unittest.TestCase):
    def test_empty_selection_does_not_open_a_browser(self) -> None:
        with patch.object(live, "run_in_browser") as runner:
            self.assertEqual(live.check_items_live([]), {})
            self.assertEqual(live.check_items_live(["", "  ", "abc"]), {})
        runner.assert_not_called()

    def test_ids_are_deduped_and_capped(self) -> None:
        seen: list[list[str]] = []

        async def fake_batch(ids):
            seen.append(list(ids))
            return {item_id: "live" for item_id in ids}

        async def fake_runner(factory, *args, **kwargs):
            return await factory(*args)

        with patch.object(live, "run_in_browser", fake_runner), \
                patch.object(live, "_verify_batch", fake_batch), \
                patch.object(live, "MAX_VERIFY", 3):
            live.check_items_live(["111", "111", "222", "333", "444"])
        self.assertEqual(seen, [["111", "222", "333"]])

    def test_batch_timeout_is_derived_from_per_item_budget(self) -> None:
        seen: dict[str, float] = {}

        async def fake_runner(factory, *args, **kwargs):
            seen["timeout"] = kwargs["timeout"]
            return {}

        with patch.object(live, "run_in_browser", fake_runner), \
                patch.object(live, "PAGE_TIMEOUT_MS", 30000), \
                patch.object(live, "BUDGET_S", 10.0):
            live.check_items_live(["111", "222", "333"])
        # 每条至少要 30s 页面超时 + 10s 探测预算；写死的 20+10n 会先把整批掐断
        self.assertGreaterEqual(seen["timeout"], 3 * (30.0 + 10.0))

    def test_batch_timeout_includes_configured_poll_interval(self) -> None:
        seen: dict[str, float] = {}

        async def fake_runner(factory, *args, **kwargs):
            seen["timeout"] = kwargs["timeout"]
            return {}

        with patch.object(live, "run_in_browser", fake_runner), \
                patch.object(live, "PAGE_TIMEOUT_MS", 500), \
                patch.object(live, "BUDGET_S", 0.1), \
                patch.object(live, "SETTLE_MS", 10000), \
                patch.object(live, "MAX_VERIFY", 8):
            live.check_items_live([str(100 + index) for index in range(8)])
        # 期限在 wait_for_timeout 之后才检查；最后一次轮询可跨过整个 SETTLE_MS。
        # 固定每条 +2s 只给整批 30.8s，但这里光轮询等待就需要 80s。
        self.assertGreaterEqual(seen["timeout"], 8 * (0.5 + 0.1 + 10.0))

    def test_non_digit_ids_are_rejected_before_browser(self) -> None:
        seen: list[list[str]] = []

        async def fake_batch(ids):
            seen.append(list(ids))
            return {}

        async def fake_runner(factory, *args, **kwargs):
            return await factory(*args)

        with patch.object(live, "run_in_browser", fake_runner), \
                patch.object(live, "_verify_batch", fake_batch):
            live.check_items_live(["111", "synthetic-noise", "12a"])
        self.assertEqual(seen, [["111"]])


class FakePage:
    def __init__(self, titles: dict[str, list[str]], fail: set[str] | None = None) -> None:
        self.titles = titles
        self.fail = fail or set()
        self.goto_calls: list[str] = []
        self._current: list[str] = []
        self.new_page_calls = 0
        self.evaluate_calls = 0

    async def goto(self, url: str, **kwargs) -> None:
        item_id = url.rsplit("id=", 1)[-1]
        self.goto_calls.append(item_id)
        if item_id in self.fail:
            raise RuntimeError("page blew up")
        self._current = list(self.titles.get(item_id, [live.GENERIC_TITLE]))

    async def wait_for_timeout(self, ms: int) -> None:
        await asyncio.sleep(0)

    async def evaluate(self, script: str):
        self.evaluate_calls += 1
        title = self._current.pop(0) if len(self._current) > 1 else self._current[0]
        return {"title": title, "len": 100}


class VerifyBatchTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, page: FakePage, ids: list[str]) -> dict[str, str]:
        class CM:
            async def __aenter__(self):
                return page

            async def __aexit__(self, *exc):
                return False

        with patch.object(live, "warm_page", lambda: CM()), \
                patch.object(live, "SETTLE_MS", 0), \
                patch.object(live, "BUDGET_S", 0.05), \
                patch.object(live, "PAGE_TIMEOUT_MS", 500):
            return await live._verify_batch(ids)

    async def test_reuses_one_page_for_the_batch(self) -> None:
        page = FakePage({"111": ["在售A_闲鱼"], "222": ["在售B_闲鱼"]})
        states = await self._run(page, ["111", "222"])
        self.assertEqual(states, {"111": "live", "222": "live"})
        self.assertEqual(page.goto_calls, ["111", "222"])

    async def test_hydration_lag_is_not_judged_offline(self) -> None:
        # 前两次探测拿到通用标题，第三次才变成商品标题：必须判 live。
        page = FakePage({"111": [live.GENERIC_TITLE, live.GENERIC_TITLE, "在售_闲鱼"]})
        states = await self._run(page, ["111"])
        self.assertEqual(states, {"111": "live"})

    async def test_never_hydrated_item_is_offline(self) -> None:
        page = FakePage({"111": [live.GENERIC_TITLE, live.GENERIC_TITLE]})
        states = await self._run(page, ["111"])
        self.assertEqual(states, {"111": "offline"})

    async def test_verification_page_trips_guard_and_stays_unknown(self) -> None:
        page = FakePage({"111": ["安全验证"]})
        with patch("goofish_z.core.guard.trip") as trip:
            states = await self._run(page, ["111"])
        self.assertEqual(states, {"111": "unknown"})
        trip.assert_called_once()
        self.assertEqual(trip.call_args.kwargs.get("api"), "item.live")

    async def test_verification_stops_before_second_item_or_another_probe(self) -> None:
        # 即便下一次探测会变成商品标题，命中验证后也不能继续探测或访问下一条。
        page = FakePage({"111": ["安全验证", "在售A_闲鱼"], "222": ["在售B_闲鱼"]})
        with patch("goofish_z.core.guard.trip") as trip:
            states = await self._run(page, ["111", "222"])
        self.assertEqual(states, {"111": "unknown", "222": "unknown"})
        self.assertEqual(page.goto_calls, ["111"])
        self.assertEqual(page.evaluate_calls, 1)
        trip.assert_called_once_with("在售校验落在登录/验证页", api="item.live")

    async def test_verification_preserves_completed_states_and_marks_rest_unknown(self) -> None:
        page = FakePage({
            "111": ["在售A_闲鱼"],
            "222": [live.GENERIC_TITLE],
            "333": ["安全验证"],
            "444": ["在售B_闲鱼"],
            "555": [live.GENERIC_TITLE],
        })
        with patch("goofish_z.core.guard.trip") as trip:
            states = await self._run(page, ["111", "222", "333", "444", "555"])
        self.assertEqual(states, {
            "111": "live", "222": "offline", "333": "unknown",
            "444": "unknown", "555": "unknown",
        })
        self.assertEqual(page.goto_calls, ["111", "222", "333"])
        trip.assert_called_once()

    async def test_verification_stops_batch_even_if_guard_recording_fails(self) -> None:
        page = FakePage({"111": ["安全验证"], "222": ["在售_闲鱼"]})
        with patch("goofish_z.core.guard.trip", side_effect=OSError("state unavailable")):
            states = await self._run(page, ["111", "222"])
        self.assertEqual(states, {"111": "unknown", "222": "unknown"})
        self.assertEqual(page.goto_calls, ["111"])
        self.assertEqual(page.evaluate_calls, 1)

    async def test_single_failure_is_unknown_and_does_not_abort_batch(self) -> None:
        page = FakePage({"222": ["在售_闲鱼"]}, fail={"111"})
        states = await self._run(page, ["111", "222"])
        self.assertEqual(states, {"111": "unknown", "222": "live"})


if __name__ == "__main__":
    unittest.main()
