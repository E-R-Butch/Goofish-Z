"""Regression tests for ACK propagation and ILLEGAL_ACCESS refresh risk.

Exercise the registered message command through CLI/MCP/API adapters and the
real mtop -> Session.refresh_token call chain with network transports mocked.
"""
import asyncio
import importlib
import json
from contextlib import contextmanager, nullcontext
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from mcp.server.mcpserver.exceptions import ToolError
from support import OfflineCase
from typer.testing import CliRunner

from goofish_z.core.errors import AuthRequiredError, RiskControlError
from goofish_z.mcp_server import make_handler


class _FakeConnect:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc_info):
        return False


@contextmanager
def _fake_message_transport(ack):
    """No credentials/network, but keep send() and _send() execution real."""
    module = importlib.import_module("goofish_z.commands.message.send")
    session = MagicMock(unb="synthetic-unb")
    with (
        patch.object(module.Session, "load", return_value=session),
        patch.object(module, "acquire", return_value=nullcontext()),
        patch.object(module, "watch", return_value=nullcontext()),
        patch.object(module, "get_access_token", return_value="synthetic-token"),
        patch.object(module, "connect", return_value=_FakeConnect()),
        patch.object(module, "register", new=AsyncMock()),
        patch.object(module, "heartbeat_loop", new=AsyncMock()),
        patch.object(module, "send_text", new=AsyncMock(return_value="MID1")),
        patch.object(module, "wait_send_ack", new=AsyncMock(return_value=ack)),
    ):
        yield module


class MessageDeliveryEntryTest(OfflineCase):
    def test_cli_timeout_has_nonzero_exit(self):
        from goofish_z.cli import app

        with _fake_message_transport(None):
            result = CliRunner().invoke(
                app, ["message", "send", "CID", "TOID", "--text", "hi", "--format", "json"]
            )
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("ack_timeout", result.output)
        self.assertIn("MID1", result.output)

    def test_cli_rejected_ack_has_nonzero_exit(self):
        from goofish_z.cli import app

        with _fake_message_transport({"code": 403}):
            result = CliRunner().invoke(
                app, ["message", "send", "CID", "TOID", "--text", "hi", "--format", "json"]
            )
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("ack_code=403", result.output)

    def test_mcp_timeout_and_rejection_become_tool_errors(self):
        for ack, detail in ((None, "ack_timeout"), ({"code": 403}, "ack_code=403")):
            with self.subTest(ack=ack), _fake_message_transport(ack) as module:
                with self.assertRaisesRegex(ToolError, detail):
                    asyncio.run(make_handler(module.send)("CID", "TOID", text="hi"))

    def test_success_keeps_cli_and_mcp_result_contract(self):
        from goofish_z.cli import app

        with _fake_message_transport({"code": 200}):
            result = CliRunner().invoke(
                app, ["message", "send", "CID", "TOID", "--text", "hi", "--format", "json"]
            )
        self.assertEqual(result.exit_code, 0, result.output)
        expected = {"cid": "CID", "toid": "TOID", "kind": "text", "ok": True, "mid": "MID1"}
        self.assertEqual(json.loads(result.stdout), expected)

        with _fake_message_transport({"code": "200"}) as module:
            actual = asyncio.run(make_handler(module.send)("CID", "TOID", text="hi"))
        self.assertEqual(actual, expected)

    def test_http_command_adapter_does_not_return_success_on_failed_ack(self):
        api = importlib.import_module("goofish_z.api.app")
        params = {"cid": "CID", "toid": "TOID", "text": "hi"}

        with _fake_message_transport({"code": 200}):
            result = api._call_command("message.send", params)
        self.assertEqual(result["ok"], True)
        self.assertEqual(set(result), {"cid", "toid", "kind", "ok", "mid"})

        with _fake_message_transport(None), self.assertRaises(HTTPException) as failure:
            api._call_command("message.send", params)
        self.assertGreaterEqual(failure.exception.status_code, 400)
        self.assertIn("ack_timeout", str(failure.exception.detail))


class IllegalAccessRefreshEntryTest(OfflineCase):
    def setUp(self):
        super().setUp()
        self.mtop = importlib.import_module("goofish_z.core.mtop")
        self.session_module = importlib.import_module("goofish_z.core.session")
        self.auth_status = importlib.import_module("goofish_z.commands.auth.status")

    def _session_with(self, *payloads):
        session = self.session_module.Session(
            http=MagicMock(), unb="synthetic-unb", tracknick="synthetic",
            device_id="synthetic-device", cookie_path=self.root / "cookies.json",
        )
        session.http.cookies.get.return_value = "synthetic-token_0"
        replies = []
        for payload in payloads:
            response = MagicMock()
            response.cookies = []
            response.json.return_value = payload
            replies.append(response)
        session.http.post.side_effect = replies
        return session

    def _signing(self):
        """Fake only the JS signing bridge; keep mtop and refresh logic real."""
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(self.mtop, "generate_sign", return_value="synthetic-sign"))
        stack.enter_context(patch.object(self.session_module, "generate_sign", return_value="synthetic-sign"))
        return stack

    def test_illegal_access_stops_browser_after_recoverable_token_error(self):
        session = self._session_with(
            {"ret": ["FAIL_SYS_TOKEN_EXOIRED::expired"]},
            {"ret": ["FAIL_SYS_ILLEGAL_ACCESS::restricted"]},
        )
        with (
            self._signing(),
            patch("goofish_z.core.refresh.is_enabled", return_value=True),
            patch("goofish_z.core.refresh.refresh_cookies_via_browser") as browser,
        ):
            with self.assertRaisesRegex(RiskControlError, "FAIL_SYS_ILLEGAL_ACCESS"):
                self.mtop.call(session, "synthetic.api", {})
        browser.assert_not_called()
        self.assertEqual(session.http.post.call_count, 2)
        self.assertTrue(self.guard.status()["tripped"])
        self.assertEqual(self.guard.status()["level"], "med")
        self.assertIn("ILLEGAL_ACCESS", self.guard.status()["reason"])

    def test_illegal_access_uses_same_classification_on_direct_mtop_path(self):
        session = self._session_with({"ret": ["FAIL_SYS_ILLEGAL_ACCESS::restricted"]})
        with self._signing(), self.assertRaises(RiskControlError):
            self.mtop.call(session, "synthetic.api", {})
        self.assertTrue(self.guard.status()["tripped"])
        self.assertEqual(self.guard.classify_level("FAIL_SYS_ILLEGAL_ACCESS"), "med")
        self.assertTrue(self.guard.looks_like_risk("FAIL_SYS_ILLEGAL_ACCESS"))

    def test_auth_status_remains_unknown_when_illegal_access_is_reported(self):
        session = self._session_with({"ret": ["FAIL_SYS_ILLEGAL_ACCESS::restricted"]})
        with (
            self._signing(),
            patch.object(self.auth_status.Session, "load", return_value=session) as load,
            patch.object(session, "refresh_token") as refresh,
            patch("goofish_z.core.refresh.refresh_cookies_via_browser") as browser,
        ):
            result = self.auth_status.status()
        self.assertEqual(result["outcome"], "risk_control")
        self.assertIsNone(result["valid"])
        load.assert_called_once_with(allow_bootstrap=False)
        refresh.assert_not_called()
        browser.assert_not_called()

    def test_global_auto_refresh_gate_still_blocks_both_refresh_paths(self):
        session = self._session_with({"ret": ["FAIL_SYS_TOKEN_EXOIRED::expired"]})
        with (
            self._signing(),
            patch("goofish_z.core.refresh.is_enabled", return_value=False),
            patch.object(session, "refresh_token") as api_refresh,
            patch("goofish_z.core.refresh.refresh_cookies_via_browser") as browser,
            self.assertRaises(AuthRequiredError),
        ):
            self.mtop.call(session, "synthetic.api", {})
        api_refresh.assert_not_called()
        browser.assert_not_called()
