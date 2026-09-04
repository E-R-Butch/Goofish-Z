"""auth status — 检查登录态是否有效。调用 mtop.taobao.idlemessage.pc.loginuser.get"""

from typing import Any
import time

from goofish_z.core import Session, Strategy, command
from goofish_z.core.mtop import call
from goofish_z.core.paths import runtime_data_path
from goofish_z.core.state_file import write_state


@command(
    namespace="auth",
    name="status",
    description="检查登录态是否有效，返回 unb / tracknick / 昵称",
    strategy=Strategy.COOKIE,
    columns=["unb", "tracknick", "nick", "valid"],
)
def status() -> dict[str, Any]:
    session = Session.load()
    try:
        raw = call(
            session,
            api="mtop.taobao.idlemessage.pc.loginuser.get",
            data={},
            version="1.0",
            spm_cnt="a21ybx.im.0.0",
        )
        user = raw.get("data", {}) or {}
        write_state(runtime_data_path("auth_status.json"), {"valid": True, "checked_at": time.time(), "error_type": None})
        return {
            "unb": session.unb,
            "tracknick": session.tracknick,
            "nick": user.get("nick", ""),
            "valid": True,
        }
    except Exception as e:  # noqa: BLE001
        write_state(runtime_data_path("auth_status.json"), {"valid": False, "checked_at": time.time(), "error_type": type(e).__name__})
        return {
            "unb": session.unb,
            "tracknick": session.tracknick,
            "nick": "",
            "valid": False,
            "error": str(e),
        }
