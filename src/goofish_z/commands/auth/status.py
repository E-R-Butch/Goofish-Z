"""auth status — 检查登录态是否有效。调用 mtop.taobao.idlemessage.pc.loginuser.get"""

import time
from typing import Any

from requests.exceptions import RequestException

from goofish_z.core import Session, Strategy, command
from goofish_z.core.errors import (
    AuthRequiredError,
    BlockedError,
    RateLimitedError,
    RiskControlError,
)
from goofish_z.core.mtop import call
from goofish_z.core.paths import runtime_data_path
from goofish_z.core.state_file import read_state, state_lock, write_state


def _record_check(valid: bool | None, outcome: str, error_type: str | None) -> None:
    """记录本次检查结果。valid 是三态：true=通过，false=需要登录，null=无法判定。

    风控、限流、网络故障都不是「登录失效」，把它们记成 false 会让用户去重新登录，
    而真正该做的是等冷却。last_success_at 只保留最近一次真正的成功。
    """
    path = runtime_data_path("auth_status.json")
    with state_lock(path):
        previous = read_state(path)
        checked_at = time.time()
        last_success = previous.get("last_success_at")
        if previous.get("valid") is True:
            last_success = previous.get("checked_at")
        write_state(path, {
            "valid": valid, "outcome": outcome, "checked_at": checked_at,
            "last_success_at": checked_at if valid is True else last_success,
            "error_type": error_type,
        })


@command(
    namespace="auth",
    name="status",
    description="检查登录态是否有效，返回 unb / tracknick / 昵称",
    strategy=Strategy.COOKIE,
    columns=["unb", "tracknick", "nick", "valid"],
)
def status() -> dict[str, Any]:
    session = None
    try:
        # 显式检查不允许顺手导入或修复登录态：否则「验证失败」会被一次隐式恢复掩盖成成功。
        session = Session.load(allow_bootstrap=False)
        raw = call(
            session,
            api="mtop.taobao.idlemessage.pc.loginuser.get",
            data={},
            version="1.0",
            spm_cnt="a21ybx.im.0.0",
            _auto_refresh=False,
        )
        user = raw.get("data", {}) or {}
        _record_check(True, "verified", None)
        return {
            "unb": session.unb,
            "tracknick": session.tracknick,
            "nick": user.get("nick", ""),
            "valid": True,
            "outcome": "verified",
        }
    except Exception as e:  # noqa: BLE001
        if isinstance(e, (RiskControlError, BlockedError)):
            outcome, valid = "risk_control", None
        elif isinstance(e, RateLimitedError):
            outcome, valid = "rate_limited", None
        elif isinstance(e, AuthRequiredError):
            outcome, valid = "auth_required", False
        elif isinstance(e, RequestException):
            outcome, valid = "network_error", None
        else:
            outcome, valid = "error", None
        _record_check(valid, outcome, type(e).__name__)
        return {
            "unb": session.unb if session else "",
            "tracknick": session.tracknick if session else "",
            "nick": "",
            "valid": valid,
            "outcome": outcome,
            "error": str(e),
        }
