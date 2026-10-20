"""Local diagnostics; never refresh credentials or send a marketplace request."""
from __future__ import annotations

import importlib.util
import math
import os
import shutil
import time
from pathlib import Path
from typing import Any

from goofish_z import __version__
from goofish_z.core import command
from goofish_z.core.guard import status as circuit_status
from goofish_z.core.limiter import status as limiter_status
from goofish_z.core.paths import runtime_data_path
from goofish_z.core.state_file import read_state

# 展示用的新鲜度，不是平台凭据的真实寿命：超过这个时间只是「不知道」，不等于失效。
AUTH_CHECK_MAX_AGE_SECONDS = 300


def _auth_summary(snapshot: dict, circuit: dict) -> dict:
    """把「上次检查记录」翻译成当前可解释的认证状态。

    历史记录 stale 时不代表掉登录，风控进行中也不代表掉登录——这些区分靠 state 字段
    表达，valid 用三态（true / false / null）保留「无法判定」。
    """
    path = runtime_data_path("cookies.json")
    try:
        modified_at = path.stat().st_mtime
    except FileNotFoundError:
        modified_at = None
    last_check = None
    if snapshot:
        last_check = {
            key: snapshot.get(key)
            for key in ("valid", "outcome", "checked_at", "error_type", "last_success_at")
        }
    checked_at = snapshot.get("checked_at")
    stamp: float | None = None
    if isinstance(checked_at, (int, float)) and not isinstance(checked_at, bool) and math.isfinite(float(checked_at)):
        stamp = float(checked_at)
    age = time.time() - stamp if stamp is not None else None
    changed = modified_at is not None and stamp is not None and modified_at > stamp
    stale = age is None or age < 0 or age > AUTH_CHECK_MAX_AGE_SECONDS or changed
    state = snapshot.get("outcome") or ("verified" if snapshot.get("valid") is True else "unknown")
    # 旧快照可能把风控/限流这类基础设施错误记成 valid=false，这里按 error_type 还原。
    legacy_errors = {
        "RiskControlError": "risk_control",
        "BlockedError": "risk_control",
        "RateLimitedError": "rate_limited",
        "AuthRequiredError": "auth_required",
    }
    if not snapshot.get("outcome") and snapshot.get("error_type"):
        state = legacy_errors.get(snapshot["error_type"], "error")
    if modified_at is None:
        state = "missing"
    elif circuit.get("tripped"):
        state = "risk_control"
    elif changed:
        state = "changed"
    elif stale and snapshot:
        state = "stale"
    valid = True if state == "verified" and not stale else False if state in ("missing", "auth_required") else None
    return {
        "cookies_present": modified_at is not None,
        "last_check": last_check,
        "state": state,
        "valid": valid,
        "stale": stale,
        "age_seconds": max(0, age) if age is not None else None,
        "max_age_seconds": AUTH_CHECK_MAX_AGE_SECONDS,
    }


def _chrome_available() -> bool:
    if shutil.which("google-chrome") or shutil.which("google-chrome-stable"):
        return True
    candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if os.environ.get(key):
            candidates.append(Path(os.environ[key]) / "Google/Chrome/Application/chrome.exe")
    return any(path.is_file() for path in candidates)


@command(namespace="auth", name="doctor", description="本地诊断依赖、登录验证记录、限流和熔断，不访问闲鱼")
def doctor() -> dict[str, Any]:
    snapshot = read_state(runtime_data_path("auth_status.json"))
    circuit = circuit_status()
    return {
        "version": __version__,
        "dependencies": {
            "playwright": importlib.util.find_spec("playwright") is not None,
            "chrome": _chrome_available(),
            "node": shutil.which("node") is not None,
        },
        "auth": _auth_summary(snapshot, circuit),
        "circuit": circuit,
        "limiter": limiter_status(),
    }
