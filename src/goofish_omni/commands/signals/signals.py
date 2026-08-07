"""signals — 自动黑名单信号引擎管理。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from goofish_omni.core.registry import command
from goofish_omni.signals import AUTO_BAN_THRESHOLD, MIN_APPEARANCES, SellerSignalDB

DEFAULT_DB = Path.home() / ".goofish-omni" / "watch.db"


def _db() -> SellerSignalDB:
    return SellerSignalDB(DEFAULT_DB)


@command(
    namespace="signals",
    name="list",
    description=f"列出卖家信号档案（信号分/出现次数/是否自动拉黑），阈值{AUTO_BAN_THRESHOLD}分+{MIN_APPEARANCES}次",
    columns=["seller_nick", "total_score", "appearances", "auto_banned", "signals_json", "last_seen_at"],
)
def signals_list(only_banned: bool = False, limit: int = 50) -> dict[str, Any]:
    return {"profiles": _db().list_profiles(only_banned=only_banned, limit=limit)}


@command(
    namespace="signals",
    name="unban",
    description="解除某个卖家的自动拉黑（误判恢复），同时清空其信号记录",
    columns=["seller_nick", "unbanned"],
)
def signals_unban(seller_nick: str) -> dict[str, Any]:
    _db().unban(seller_nick)
    return {"seller_nick": seller_nick, "unbanned": True}
