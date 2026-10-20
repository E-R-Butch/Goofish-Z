"""发送回执（ACK）行为。

回归目标：message send 过去只 sleep 1 秒就返回 ok=True，从不读回执 ——
服务端拒收或限流也报成功。现在必须收到 mid 匹配的 code=200，否则如实报失败。
"""

import asyncio
import importlib
import json

from support import OfflineCase


class FakeWS:
    """最小 WS 替身：按序吐出 frames，耗尽后挂起以触发超时。"""

    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []

    async def recv(self):
        if not self.frames:
            await asyncio.sleep(3600)
        return self.frames.pop(0)

    async def send(self, data):
        self.sent.append(data)


class MessageAckTest(OfflineCase):
    def setUp(self):
        super().setUp()
        self.ws_mod = importlib.import_module("goofish_z.core.ws")
        self.send_mod = importlib.import_module("goofish_z.commands.message.send")

    def test_matching_mid_returns_ack(self):
        frame = json.dumps({"code": 200, "headers": {"mid": "MID1", "sid": "S"}})
        ack = asyncio.run(self.ws_mod.wait_send_ack(FakeWS([frame]), "MID1", timeout=1.0))
        self.assertIsNotNone(ack)
        self.assertEqual(ack["code"], 200)

    def test_unrelated_mid_is_ignored_until_timeout(self):
        frame = json.dumps({"code": 200, "headers": {"mid": "OTHER"}})
        ack = asyncio.run(self.ws_mod.wait_send_ack(FakeWS([frame]), "MID1", timeout=0.3))
        self.assertIsNone(ack)

    def test_timeout_returns_none(self):
        ack = asyncio.run(self.ws_mod.wait_send_ack(FakeWS([]), "MID1", timeout=0.3))
        self.assertIsNone(ack)

    def test_non_json_frames_are_skipped(self):
        frames = ["not-json", json.dumps({"code": 200, "headers": {"mid": "MID1"}})]
        ack = asyncio.run(self.ws_mod.wait_send_ack(FakeWS(frames), "MID1", timeout=1.0))
        self.assertIsNotNone(ack)

    def test_incoming_frame_is_acked_downstream(self):
        frame = json.dumps({"code": 200, "headers": {"mid": "MID1", "sid": "S"}})
        ws = FakeWS([frame])
        asyncio.run(self.ws_mod.wait_send_ack(ws, "MID1", timeout=1.0))
        self.assertEqual(len(ws.sent), 1)
        self.assertEqual(json.loads(ws.sent[0])["code"], 200)

    # ---------- 结果映射 ----------

    def test_missing_ack_is_a_failure(self):
        result = self.send_mod._apply_ack({"ok": False, "mid": "M"}, None)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "ack_timeout")

    def test_code_200_is_success(self):
        result = self.send_mod._apply_ack({"ok": False, "mid": "M"}, {"code": 200})
        self.assertTrue(result["ok"])
        self.assertNotIn("error", result)

    def test_string_code_200_is_success(self):
        result = self.send_mod._apply_ack({"ok": False, "mid": "M"}, {"code": "200"})
        self.assertTrue(result["ok"])

    def test_rejected_ack_is_a_failure(self):
        result = self.send_mod._apply_ack({"ok": False, "mid": "M"}, {"code": 403})
        self.assertFalse(result["ok"])
        self.assertIn("403", result["error"])

    def test_ack_without_code_is_a_failure(self):
        result = self.send_mod._apply_ack({"ok": False, "mid": "M"}, {})
        self.assertFalse(result["ok"])
        self.assertIn("ack_code", result["error"])
