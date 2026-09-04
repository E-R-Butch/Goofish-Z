"""Local diagnostics; never refresh credentials or send a marketplace request."""
from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

from goofish_z import __version__
from goofish_z.core import command
from goofish_z.core.guard import status as circuit_status
from goofish_z.core.limiter import status as limiter_status
from goofish_z.core.paths import runtime_data_path
from goofish_z.core.state_file import read_state


def _chrome_available() -> bool:
    if shutil.which("google-chrome") or shutil.which("google-chrome-stable"):
        return True
    candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if os.environ.get(key):
            candidates.append(Path(os.environ[key]) / "Google/Chrome/Application/chrome.exe")
    return any(path.is_file() for path in candidates)


@command(namespace="auth", name="doctor", description="本地诊断依赖、登录验证记录、限流和熔断，不访问闲鱼")
def doctor() -> dict:
    snapshot = read_state(runtime_data_path("auth_status.json"))
    last_check = None
    if snapshot:
        last_check = {key: snapshot.get(key) for key in ("valid", "checked_at", "error_type")}
    return {
        "version": __version__,
        "dependencies": {
            "playwright": importlib.util.find_spec("playwright") is not None,
            "chrome": _chrome_available(),
            "node": shutil.which("node") is not None,
        },
        "auth": {"cookies_present": runtime_data_path("cookies.json").is_file(), "last_check": last_check},
        "circuit": circuit_status(),
        "limiter": limiter_status(),
    }
