"""认证状态的三态语义。

回归目标：`valid` 只有 true/false 会把风控、限流、网络故障一律显示成「登录失效」，
把用户推去做无意义的重新登录。现在 valid 是 true/false/null 三态，outcome 给出分类；
doctor 的 state 再叠加新鲜度与熔断，让「历史成功」不外溢成「当前可用」。
"""

import importlib
import os
import time
from unittest.mock import MagicMock, patch

from requests.exceptions import ConnectionError as RequestsConnectionError
from support import OfflineCase

from goofish_z.core.state_file import file_version


class AuthStatusOutcomeTest(OfflineCase):
    def setUp(self):
        super().setUp()
        self.status_mod = importlib.import_module("goofish_z.commands.auth.status")
        self.errors = importlib.import_module("goofish_z.core.errors")
        self.session = MagicMock(unb="synthetic-unb", tracknick="synthetic-nick")

    def _run(self, *, error=None, result=None):
        load = patch.object(self.status_mod.Session, "load", return_value=self.session)
        call = patch.object(self.status_mod, "call", side_effect=error) if error else patch.object(
            self.status_mod, "call", return_value=result or {"data": {"nick": "synthetic"}}
        )
        with load, call:
            return self.status_mod.status()

    def test_success_is_verified_and_true(self):
        out = self._run(result={"data": {"nick": "synthetic"}})
        self.assertEqual(out["outcome"], "verified")
        self.assertIs(out["valid"], True)

    def test_risk_control_is_unknown_not_logged_out(self):
        out = self._run(error=self.errors.RiskControlError("RGV587_ERROR"))
        self.assertEqual(out["outcome"], "risk_control")
        self.assertIsNone(out["valid"])

    def test_rate_limit_is_unknown(self):
        out = self._run(error=self.errors.RateLimitedError("slow down", retry_after=30))
        self.assertEqual(out["outcome"], "rate_limited")
        self.assertIsNone(out["valid"])

    def test_auth_required_is_the_only_explicit_false(self):
        out = self._run(error=self.errors.AuthRequiredError("expired"))
        self.assertEqual(out["outcome"], "auth_required")
        self.assertIs(out["valid"], False)

    def test_network_error_is_unknown(self):
        out = self._run(error=RequestsConnectionError("dns"))
        self.assertEqual(out["outcome"], "network_error")
        self.assertIsNone(out["valid"])

    def test_unexpected_error_is_unknown(self):
        out = self._run(error=ValueError("boom"))
        self.assertEqual(out["outcome"], "error")
        self.assertIsNone(out["valid"])

    def test_explicit_check_never_bootstraps(self):
        """auth status 必须关掉自动导入：否则一次隐式恢复会把失败掩盖成成功。"""
        with patch.object(self.status_mod.Session, "load", wraps=None) as load:
            load.return_value = self.session
            with patch.object(self.status_mod, "call", return_value={"data": {}}):
                self.status_mod.status()
        self.assertEqual(load.call_args.kwargs.get("allow_bootstrap"), False)


class AuthSummaryStateTest(OfflineCase):
    def setUp(self):
        super().setUp()
        self.doctor = importlib.import_module("goofish_z.commands.auth.doctor")
        self.cookies = self.root / "cookies.json"

    def _write_cookies(self, age_seconds: float = 0.0) -> None:
        self.cookies.write_text("[]", encoding="utf-8")
        if age_seconds:
            stamp = time.time() - age_seconds
            os.utime(self.cookies, (stamp, stamp))

    def test_missing_cookie_file_is_explicit_false(self):
        summary = self.doctor._auth_summary({}, {"tripped": False})
        self.assertEqual(summary["state"], "missing")
        self.assertIs(summary["valid"], False)

    def test_fresh_verified_snapshot_is_true(self):
        self._write_cookies()
        checked = time.time() - 5
        os.utime(self.cookies, (checked - 10, checked - 10))
        summary = self.doctor._auth_summary(
            {"valid": True, "outcome": "verified", "checked_at": checked,
             "cookie_version": file_version(self.cookies)}, {"tripped": False}
        )
        self.assertEqual(summary["state"], "verified")
        self.assertIs(summary["valid"], True)
        self.assertFalse(summary["stale"])

    def test_old_snapshot_becomes_stale_and_unknown(self):
        self._write_cookies()
        checked = time.time() - 3600
        os.utime(self.cookies, (checked - 10, checked - 10))
        summary = self.doctor._auth_summary(
            {"valid": True, "outcome": "verified", "checked_at": checked,
             "cookie_version": file_version(self.cookies)}, {"tripped": False}
        )
        self.assertEqual(summary["state"], "stale")
        self.assertIsNone(summary["valid"])
        self.assertTrue(summary["stale"])

    def test_cookie_file_newer_than_check_means_changed(self):
        self._write_cookies()
        checked = time.time() - 60
        checked_version = file_version(self.cookies)
        os.utime(self.cookies, (time.time(), time.time()))
        summary = self.doctor._auth_summary(
            {"valid": True, "outcome": "verified", "checked_at": checked,
             "cookie_version": checked_version}, {"tripped": False}
        )
        self.assertEqual(summary["state"], "changed")
        self.assertIsNone(summary["valid"])

    def test_active_circuit_overrides_history(self):
        self._write_cookies()
        checked = time.time() - 5
        os.utime(self.cookies, (checked - 10, checked - 10))
        summary = self.doctor._auth_summary(
            {"valid": True, "outcome": "verified", "checked_at": checked,
             "cookie_version": file_version(self.cookies)},
            {"tripped": True, "remaining_seconds": 60},
        )
        self.assertEqual(summary["state"], "risk_control")
        self.assertIsNone(summary["valid"])

    def test_legacy_risk_snapshot_is_not_reported_as_logged_out(self):
        """旧版本把风控写成 valid=false，读回时要还原成 risk_control。"""
        self._write_cookies()
        checked = time.time() - 5
        os.utime(self.cookies, (checked - 10, checked - 10))
        summary = self.doctor._auth_summary(
            {"valid": False, "checked_at": checked, "error_type": "RiskControlError"},
            {"tripped": False},
        )
        self.assertEqual(summary["state"], "stale")
        self.assertEqual(summary["last_check"]["error_type"], "RiskControlError")
        self.assertIsNone(summary["valid"])
