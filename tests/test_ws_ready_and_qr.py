"""IM 就绪门与扫码页定位的离线契约测试。

两个都是「线上才暴露」的坑，用假对象把判据钉住：

* `/r/` 必须在 `/s/vulcan` 之后才发（否则服务端 code 400），`/reg` 200 不算就绪。
* passport iframe 要按 URL 轮询 `page.frames`，QR 等 `.qrcode-login` 而不是 canvas。
"""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from goofish_z.commands.message import send as send_mod
from goofish_z.core import qr_login
from goofish_z.core import ws as ws_mod
from goofish_z.core.errors import GoofishError


class FakeWS:
    """最简 LWP 连接：send 记录出站帧，recv 按队列出帧，空了就抛 TimeoutError。"""

    def __init__(self, frames: list[dict] | None = None, stalls: float = 0.0) -> None:
        self.sent: list[dict] = []
        self._frames = list(frames or [])
        self._stalls = stalls

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def recv(self) -> str:
        if self._frames:
            return json.dumps(self._frames.pop(0))
        await asyncio.sleep(self._stalls or 0.01)
        raise TimeoutError


class WaitReadyTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_true_when_vulcan_arrives(self) -> None:
        client = FakeWS([{"lwp": "/s/vulcan", "headers": {"mid": "srv-1"}}])
        self.assertTrue(await ws_mod.wait_ready(client, timeout=1))

    async def test_acks_downstream_frames_while_waiting(self) -> None:
        client = FakeWS([
            {"lwp": "/s/ping", "headers": {"mid": "m1", "sid": "s1"}},
            {"lwp": "/s/vulcan", "headers": {"mid": "srv-2"}},
        ])
        self.assertTrue(await ws_mod.wait_ready(client, timeout=1))
        acks = [frame for frame in client.sent if frame.get("code") == 200]
        self.assertEqual(len(acks), 2)
        self.assertEqual(acks[0]["headers"]["mid"], "m1")

    async def test_times_out_without_vulcan(self) -> None:
        client = FakeWS([])
        self.assertFalse(await ws_mod.wait_ready(client, timeout=0.05))

    async def test_register_failure_raises(self) -> None:
        client = FakeWS([{"headers": {"mid": "reg-1"}, "code": 403}])
        with self.assertRaises(GoofishError) as ctx:
            await ws_mod.wait_ready(client, mids={"reg": "reg-1"}, timeout=1)
        self.assertIn("/reg", str(ctx.exception))

    async def test_ackdiff_failure_is_not_fatal(self) -> None:
        client = FakeWS([
            {"headers": {"mid": "ack-1"}, "code": 400},
            {"lwp": "/s/vulcan", "headers": {"mid": "srv-3"}},
        ])
        self.assertTrue(await ws_mod.wait_ready(client, mids={"ack_diff": "ack-1"}, timeout=1))

    async def test_register_returns_handshake_mids(self) -> None:
        client = FakeWS([{"lwp": "/s/vulcan", "headers": {"mid": "srv-4"}}])
        mids = await ws_mod.register(client, _FakeSession(), "token")
        self.assertEqual(set(mids), {"reg", "ack_diff"})
        self.assertEqual(client.sent[0]["headers"]["mid"], mids["reg"])
        self.assertEqual(client.sent[1]["headers"]["mid"], mids["ack_diff"])


class _FakeSession:
    device_id = "synthetic-device"

    class http:  # noqa: N801 - 只用于 _cookie_header 的存在性
        pass


class SendGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_send_waits_for_readiness_before_any_r_request(self) -> None:
        client = FakeWS([])
        order: list[str] = []

        class CM:
            async def __aenter__(self):
                return client

            async def __aexit__(self, *exc):
                return False

        async def fake_register(ws, session, token):
            order.append("register")
            return {"reg": "r", "ack_diff": "a"}

        async def fake_wait_ready(ws, *, mids=None, timeout=15.0):
            order.append("wait_ready")
            return True

        async def fake_send_text(ws, **kwargs):
            order.append("send_text")
            return "mid-1"

        async def fake_ack(ws, mid, timeout=5.0):
            return {"code": 200, "headers": {"mid": mid}}

        with (
            patch.object(send_mod, "connect", lambda s: CM()),
            patch.object(send_mod, "register", fake_register),
            patch.object(send_mod, "wait_ready", fake_wait_ready),
            patch.object(send_mod, "send_text", fake_send_text),
            patch.object(send_mod, "wait_send_ack", fake_ack),
            patch.object(send_mod, "get_access_token", lambda s: "t"),
        ):
            result = await send_mod._send(
                _SendSession(), cid="cid-1", toid="peer-1", text="hi",
                kind="text", image_url="", image_width=0, image_height=0, item_id="",
            )
        self.assertEqual(order, ["register", "wait_ready", "send_text"])
        self.assertTrue(result["ok"])

    async def test_not_ready_aborts_without_sending(self) -> None:
        client = FakeWS([])
        sent: list[str] = []

        class CM:
            async def __aenter__(self):
                return client

            async def __aexit__(self, *exc):
                return False

        async def fake_send_text(ws, **kwargs):
            sent.append("sent")
            return "mid-1"

        with (
            patch.object(send_mod, "connect", lambda s: CM()),
            patch.object(send_mod, "register", lambda *a: _coro({"reg": "r"})),
            patch.object(send_mod, "wait_ready", lambda *a, **k: _coro(False)),
            patch.object(send_mod, "send_text", fake_send_text),
            patch.object(send_mod, "get_access_token", lambda s: "t"),
        ):
            with self.assertRaises(GoofishError) as ctx:
                await send_mod._send(
                    _SendSession(), cid="cid-1", toid="peer-1", text="hi",
                    kind="text", image_url="", image_width=0, image_height=0, item_id="",
                )
        self.assertIn("/s/vulcan", str(ctx.exception))
        self.assertEqual(sent, [])


async def _coro(value):
    return value


class _SendSession:
    unb = "000000001"
    device_id = "synthetic-device"


class QrFrameTest(unittest.IsolatedAsyncioTestCase):
    async def test_finds_passport_frame_by_url(self) -> None:
        passport = _Frame("https://passport.goofish.com/mini_login.htm", has_qr=True)
        page = _Page([_Frame("about:blank"), passport])
        self.assertIs(await qr_login._find_passport_frame(page, timeout_ms=500), passport)

    async def test_returns_none_when_no_passport_frame(self) -> None:
        page = _Page([_Frame("about:blank")])
        self.assertIsNone(await qr_login._find_passport_frame(page, timeout_ms=300))

    async def test_qr_waits_for_container_not_canvas(self) -> None:
        frame = _Frame("https://passport.goofish.com/mini_login.htm", has_qr=True)
        page = _Page([frame])
        self.assertTrue(await qr_login._wait_for_qr(page, timeout_ms=500))
        self.assertEqual(frame.selectors, [".qrcode-login"])

    async def test_missing_qr_container_is_reported_false(self) -> None:
        frame = _Frame("https://passport.goofish.com/mini_login.htm", has_qr=False)
        page = _Page([frame])
        self.assertFalse(await qr_login._wait_for_qr(page, timeout_ms=300))


class _Frame:
    def __init__(self, url: str, has_qr: bool = False) -> None:
        self.url = url
        self._has_qr = has_qr
        self.selectors: list[str] = []

    async def wait_for_selector(self, selector: str, timeout: int = 0):
        self.selectors.append(selector)
        if not self._has_qr:
            raise TimeoutError(selector)
        return object()


class _Page:
    def __init__(self, frames: list[_Frame]) -> None:
        self.frames = frames


if __name__ == "__main__":
    unittest.main()
