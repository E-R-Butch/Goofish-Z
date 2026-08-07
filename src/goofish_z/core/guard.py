"""风控熔断系统 — 分级冷却 + 触发统计 + 自动恢复。

触发 RiskControlError 时按严重度分级熔断：
- LEVEL_HARD (RGV587/哎哟喂/被挤爆)  → 30 分钟（账号级重罚，硬停）
- LEVEL_MED  (FAIL_SYS_USER_VALIDATE) → 10 分钟（接口级风控）
- LEVEL_SOFT (其他风控关键词)         → 2 分钟（轻触）

状态文件：~/.goofish-z/circuit.json（含 until/level/reason/api/hits 统计）
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from goofish_z.core.errors import RiskControlError

# 数据目录与 goofish-omni 统一（旧版 ~/.goofish-cli/ 已废弃）
DATA_DIR = Path(os.environ.get("GOOFISH_Z_DATA", str(Path.home() / ".goofish-z")))
STATE_PATH = DATA_DIR / "circuit.json"

# 分级冷却（秒）
LEVELS = {
    "hard": 30 * 60,   # RGV587 → 30 分钟
    "med": 10 * 60,    # USER_VALIDATE → 10 分钟
    "soft": 2 * 60,    # 其他 → 2 分钟
}
DEFAULT_LEVEL = "med"

# 硬风控关键词 → hard；接口风控 → med；其余 → soft
_HARD_KEYWORDS = ("RGV587", "哎哟喂", "被挤爆", "punish", "PUNISH")
_MED_KEYWORDS = ("FAIL_SYS_USER_VALIDATE", "USER_VALIDATE", "验证码", "滑块", "安全验证", "异常访问")


def classify_level(ret_str: str) -> str:
    """根据风控响应文本判定等级。"""
    for kw in _HARD_KEYWORDS:
        if kw in ret_str:
            return "hard"
    for kw in _MED_KEYWORDS:
        if kw in ret_str:
            return "med"
    return "soft"


def _break_seconds(level: str) -> int:
    # 环境变量可覆盖（GOOFISH_CIRCUIT_BREAK_MINUTES 兼容旧配置）
    env = os.environ.get("GOOFISH_CIRCUIT_BREAK_MINUTES")
    if env:
        try:
            return max(60, int(env)) * 60
        except ValueError:
            pass
    return LEVELS.get(level, LEVELS[DEFAULT_LEVEL])


def _load() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def check() -> None:
    """熔断检查。熔断期内抛 RiskControlError。"""
    state = _load()
    until = float(state.get("until", 0) or 0)
    if until and time.time() < until:
        remain = int(until - time.time())
        raise RiskControlError(
            f"风控熔断中（{state.get('level','med')}级），剩余 {remain}s。"
            f"原因：{state.get('reason','')}。"
            f"可 `goofish-omni auth reset-guard` 手动解除。"
        )


def trip(reason: str = "", api: str = "") -> None:
    """触发熔断。按风控等级冷却，并记录统计。"""
    level = classify_level(reason)
    until = time.time() + _break_seconds(level)
    state = _load()
    hits = int(state.get("hits", 0)) + 1
    _save({
        "until": until,
        "level": level,
        "reason": reason[:200],
        "api": api,
        "tripped_at": time.time(),
        "hits": hits,
    })


def reset() -> None:
    if STATE_PATH.exists():
        STATE_PATH.unlink()


def status() -> dict:
    """当前熔断状态（供 auth status / 监控面板展示）。"""
    state = _load()
    until = float(state.get("until", 0) or 0)
    remaining = max(0, int(until - time.time())) if until else 0
    return {
        "tripped": remaining > 0,
        "level": state.get("level", ""),
        "reason": state.get("reason", ""),
        "api": state.get("api", ""),
        "remaining_seconds": remaining,
        "total_hits": int(state.get("hits", 0)),
        "tripped_at": state.get("tripped_at"),
    }


@contextmanager
def watch(api: str = ""):
    """包住请求：命中风控自动熔断并记录来源 API。"""
    check()
    try:
        yield
    except RiskControlError as e:
        trip(str(e), api=api)
        raise
