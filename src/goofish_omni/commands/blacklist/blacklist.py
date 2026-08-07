"""blacklist — 劣质商家屏蔽规则。过滤 search/watch 结果。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from goofish_omni.blacklist import BlacklistDB
from goofish_omni.core.registry import command

DEFAULT_DB = Path.home() / ".goofish-omni" / "watch.db"


def _db() -> BlacklistDB:
    return BlacklistDB(DEFAULT_DB)


@command(
    namespace="blacklist",
    name="add",
    description="添加屏蔽规则: title_keyword=标题关键词 / location=地区 / no_badge=无信用标识 / price_drop=累计降价阈值%",
    columns=["id", "kind", "value", "note", "enabled"],
)
def blacklist_add(kind: str, value: str, note: str = "") -> dict[str, Any]:
    kind = kind.lower().strip()
    valid = {"title_keyword", "location", "no_badge", "price_drop", "seller_nick", "price_anomaly"}
    if kind not in valid:
        raise ValueError(f"kind 必须是 {sorted(valid)}")
    if kind == "no_badge":
        value = "1"
    rid = _db().add_rule(kind, value, note)
    return {"added": {"id": rid, "kind": kind, "value": value, "note": note}}


@command(
    namespace="blacklist",
    name="list",
    description="列出所有屏蔽规则",
    columns=["id", "kind", "value", "note", "enabled"],
)
def blacklist_list() -> dict[str, Any]:
    return {"rules": _db().list_rules()}


@command(
    namespace="blacklist",
    name="remove",
    description="删除屏蔽规则",
    columns=["removed", "rule_id"],
)
def blacklist_remove(rule_id: int) -> dict[str, Any]:
    ok = _db().remove_rule(rule_id)
    return {"removed": ok, "rule_id": rule_id}


@command(
    namespace="blacklist",
    name="test",
    description="用一条商品标题测试哪些规则会命中（调试用）",
    columns=["title", "reasons"],
)
def blacklist_test(title: str, location: str = "", badge: str = "", original_price: str = "") -> dict[str, Any]:
    item = {
        "title": title,
        "location": location,
        "badge": badge,
        "original_price": original_price,
    }
    passed, blocked = _db().filter_items([item])
    if blocked:
        return {"title": title, "blocked": True, "reasons": blocked[0].get("_blocked_reasons", [])}
    return {"title": title, "blocked": False, "reasons": []}
