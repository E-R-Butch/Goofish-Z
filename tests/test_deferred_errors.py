"""Offline regressions for upstream error mapping and credential-check races."""

import importlib
import json
import os
import time
from email.utils import formatdate
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from support import OfflineCase

from goofish_z.core.errors import (
    AuthRequiredError,
    GoofishError,
    RateLimitedError,
    UpstreamError,
)
from goofish_z.core.state_file import file_version, read_state


class UpstreamErrorMappingTest(OfflineCase):
    def setUp(self):
        super().setUp()
        self.api = importlib.import_module("goofish_z.api.app")
        self.mtop = importlib.import_module("goofish_z.core.mtop")
        self.session = MagicMock(h5_token="synthetic-token")
        self.response = self.session.http.post.return_value
        self.response.status_code = 200
        self.response.headers = {}
        self.response.json.return_value = {"ret": ["SUCCESS::ok"]}
        self.use(patch.object(self.mtop, "generate_sign", return_value="synthetic-sign"))

    def _call(self):
        return self.mtop.call(self.session, "api.synthetic", {})

    def _http(self, command):
        with patch.object(self.api, "_all_commands", return_value={
            "message.list-chats": SimpleNamespace(func=command),
        }):
            return TestClient(self.api.app).get("/api/message/chats")

    def _assert_no_recovery(self):
        self.session.refresh_token.assert_not_called()
        self.assertFalse(self.guard.status()["tripped"])

    def test_upstream_contract_error_is_http_502(self):
        self.response.json.return_value = {}
        response = self._http(self._call)
        self.assertEqual(response.status_code, 502)
        self._assert_no_recovery()

    def test_generic_command_error_remains_http_400(self):
        def command():
            raise GoofishError("synthetic failure")
        self.assertEqual(self._http(command).status_code, 400)

    def test_invalid_json_is_upstream_error(self):
        self.response.json.side_effect = ValueError("synthetic HTML")
        with self.assertRaises(UpstreamError):
            self._call()
        self.assertEqual(self._http(self._call).status_code, 502)
        self._assert_no_recovery()

    def test_http_429_is_classified_before_json_and_preserves_delay(self):
        self.response.status_code = 429
        self.response.headers = {"Retry-After": "12.0001"}
        self.response.json.side_effect = ValueError("synthetic HTML")
        response = self._http(self._call)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "13")
        self.response.json.assert_not_called()
        self._assert_no_recovery()

    def test_http_429_without_valid_retry_after_uses_safe_fallback(self):
        self.response.status_code = 429
        for value in (None, "", "invalid", "nan", "inf", "-inf"):
            with self.subTest(value=value):
                self.response.headers = {"Retry-After": value}
                response = self._http(self._call)
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.headers["Retry-After"], "1")
        self._assert_no_recovery()

    def test_retry_after_http_date(self):
        now = int(time.time())
        with patch.object(self.mtop.time, "time", return_value=now):
            self.assertEqual(self.mtop._retry_after_seconds(formatdate(now + 30, usegmt=True)), 30)
            self.assertEqual(self.mtop._retry_after_seconds(formatdate(now - 30, usegmt=True)), 0)
        self.assertEqual(self.mtop._retry_after_seconds("-5"), 0)

    def test_mtop_traffic_limit_is_not_auth_failure_or_risk_control(self):
        self.response.json.return_value = {"ret": ["FAIL_SYS_TRAFFIC_LIMIT::slow down"]}
        with self.assertRaises(RateLimitedError) as caught:
            self._call()
        self.assertEqual(caught.exception.raw, self.response.json.return_value)
        self.assertEqual(self._http(self._call).status_code, 429)
        self._assert_no_recovery()

    def test_auth_status_recognizes_real_mtop_limit(self):
        status = importlib.import_module("goofish_z.commands.auth.status")
        self.session.unb = "synthetic-unb"
        self.session.tracknick = "synthetic-nick"
        for status_code, payload in (
            (429, None), (200, {"ret": ["FAIL_SYS_TRAFFIC_LIMIT::slow down"]}),
        ):
            with self.subTest(status_code=status_code):
                self.response.status_code = status_code
                self.response.json.return_value = payload
                with patch.object(status.Session, "load", return_value=self.session):
                    result = status.status()
                self.assertIsNone(result["valid"])
                self.assertEqual(result["outcome"], "rate_limited")
        self._assert_no_recovery()


class AuthCredentialVersionTest(OfflineCase):
    def setUp(self):
        super().setUp()
        self.status = importlib.import_module("goofish_z.commands.auth.status")
        self.doctor = importlib.import_module("goofish_z.commands.auth.doctor")
        self.cookies = self.root / "cookies.json"
        self.cookies.write_text(json.dumps({"unb": "synthetic-old", "_m_h5_tk": "synthetic-token"}))
        self.session = SimpleNamespace(unb="synthetic-old", tracknick="synthetic-nick")

    def _summary(self):
        snapshot = read_state(self.root / "auth_status.json")
        return self.doctor._auth_summary(snapshot, {"tripped": False})

    def _replace_cookies(self):
        old = self.cookies.stat()
        replacement = self.root / "replacement.json"
        replacement.write_text(json.dumps({"unb": "synthetic-new", "_m_h5_tk": "synthetic-token"}))
        # Preserving mtime defeats the previous time-only check, but not identity.
        os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
        replacement.replace(self.cookies)

    def _check(self, call):
        with patch.object(self.status.Session, "load", return_value=self.session), \
             patch.object(self.status, "call", side_effect=call):
            return self.status.status()

    def test_unchanged_credentials_remain_verified(self):
        self._check(lambda *args, **kwargs: {"data": {}})
        snapshot = read_state(self.root / "auth_status.json")
        self.assertEqual(snapshot["cookie_version"], file_version(self.cookies))
        self.assertNotIn("synthetic-token", json.dumps(snapshot))
        summary = self._summary()
        self.assertEqual(summary["state"], "verified")
        self.assertIs(summary["valid"], True)

    def test_cookie_replacement_during_success_is_not_verified(self):
        def call(*args, **kwargs):
            self._replace_cookies()
            return {"data": {}}
        result = self._check(call)
        self.assertIs(result["valid"], True)  # The request verified the old account only.
        self.assertEqual(result["unb"], "synthetic-old")
        summary = self._summary()
        self.assertEqual(summary["state"], "changed")
        self.assertIsNone(summary["valid"])
        self.assertTrue(summary["stale"])

    def test_cookie_replacement_during_failure_is_not_logged_out(self):
        def call(*args, **kwargs):
            self._replace_cookies()
            raise AuthRequiredError("old credentials expired")
        self._check(call)
        summary = self._summary()
        self.assertEqual(summary["state"], "changed")
        self.assertIsNone(summary["valid"])

    def test_cookie_replacement_while_loading_is_unverified(self):
        def load(*args, **kwargs):
            self._replace_cookies()
            return self.session
        with patch.object(self.status.Session, "load", side_effect=load), \
             patch.object(self.status, "call", return_value={"data": {}}):
            self.status.status()
        self.assertIsNone(read_state(self.root / "auth_status.json")["cookie_version"])
        self.assertEqual(self._summary()["state"], "stale")
        self.assertIsNone(self._summary()["valid"])

    def test_failed_load_records_revision_without_bootstrapping(self):
        with patch.object(self.status.Session, "load", side_effect=AuthRequiredError("expired")), \
             patch.object(self.status, "call") as call:
            self.status.status()
        call.assert_not_called()
        self.assertEqual(self._summary()["state"], "auth_required")
        self.assertIs(self._summary()["valid"], False)

    def test_replacement_during_failed_load_is_not_logged_out(self):
        def load(**kwargs):
            self._replace_cookies()
            raise AuthRequiredError("old credentials expired")
        with patch.object(self.status.Session, "load", side_effect=load), \
             patch.object(self.status, "call") as call:
            self.status.status()
        call.assert_not_called()
        self.assertEqual(self._summary()["state"], "changed")
        self.assertIsNone(self._summary()["valid"])

    def test_legacy_or_invalid_revision_never_reports_verified(self):
        for version in (None, [], "legacy", [True] * 5):
            with self.subTest(version=version):
                snapshot = {"valid": True, "outcome": "verified", "checked_at": time.time()}
                if version is not None:
                    snapshot["cookie_version"] = version
                summary = self.doctor._auth_summary(snapshot, {"tripped": False})
                self.assertEqual(summary["state"], "stale")
                self.assertIsNone(summary["valid"])
