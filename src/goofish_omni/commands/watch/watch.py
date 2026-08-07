"""watch — 价格监控（生态空白点）。定时搜索 + SQLite 落盘 + 告警。"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from goofish_omni.core.registry import command
from goofish_omni.db import WatchDB

DEFAULT_DB = Path.home() / ".goofish-omni" / "watch.db"


def _db() -> WatchDB:
    return WatchDB(DEFAULT_DB)


@command(
    namespace="watch",
    name="add",
    description="添加价格监控关键词（可带 max_price 低价告警线）",
    columns=["id", "keyword", "max_price", "min_price", "created_at"],
)
def watch_add(keyword: str, max_price: float | None = None, min_price: float | None = None) -> dict[str, Any]:
    db = _db()
    wid = db.add_watch(keyword, max_price=max_price, min_price=min_price)
    row = db.get_watch(wid)
    return {"added": row}


@command(
    namespace="watch",
    name="list",
    description="列出所有监控项及最新检查时间",
    columns=["id", "keyword", "max_price", "min_price", "enabled", "last_check_at"],
)
def watch_list() -> dict[str, Any]:
    return {"watches": _db().list_watches()}


@command(
    namespace="watch",
    name="remove",
    description="删除监控项（含其历史）",
    columns=["removed"],
)
def watch_remove(watch_id: int) -> dict[str, Any]:
    ok = _db().remove_watch(watch_id)
    return {"removed": ok, "watch_id": watch_id}


@command(
    namespace="watch",
    name="enable",
    description="启用/停用监控项",
    columns=["watch_id", "enabled"],
)
def watch_enable(watch_id: int, enabled: bool = True) -> dict[str, Any]:
    _db().set_watch_enabled(watch_id, enabled)
    return {"watch_id": watch_id, "enabled": enabled}


@command(
    namespace="watch",
    name="history",
    description="查看某个监控项的价格历史（按商品聚合）",
    columns=["item_id", "title", "price", "checked_at", "location", "url"],
)
def watch_history(watch_id: int, limit: int = 50) -> dict[str, Any]:
    rows = _db().history(watch_id, limit=limit)
    return {"watch_id": watch_id, "items": rows, "count": len(rows)}


@command(
    namespace="watch",
    name="run",
    description="立即对某监控项执行一次搜索并落盘（或 --all 跑全部启用项）",
    columns=["watch_id", "keyword", "captured", "alerts"],
)
def watch_run(watch_id: int | None = None, all: bool = False, limit: int = 20) -> dict[str, Any]:
    """执行一次监控轮询。

    依赖 search 命令做实际抓取；这里负责调度 + 落盘 + 告警判定。
    """
    from goofish_omni.commands.search.search import search as search_cmd
    from goofish_omni.db import _to_float

    db = _db()
    if all:
        targets = [w for w in db.list_watches() if w["enabled"]]
    elif watch_id is not None:
        w = db.get_watch(watch_id)
        targets = [w] if w else []
    else:
        raise ValueError("需要 watch_id 或 --all")

    results = []
    from goofish_omni.blacklist import BlacklistDB

    bdb = BlacklistDB(Path.home() / ".goofish-omni" / "watch.db")
    for w in targets:
        try:
            items = search_cmd(str(w["keyword"]), limit=limit).get("items", [])
        except Exception as e:
            results.append({"watch_id": w["id"], "keyword": w["keyword"], "error": str(e)})
            continue

        # 黑名单过滤：屏蔽劣质商家，只对通过的商品落盘+告警
        passed, blocked = bdb.filter_items(items)
        db.record_items(w["id"], passed)
        db.touch_check(w["id"])

        # 告警判定（只针对通过黑名单的商品）
        alerts = []
        for it in passed:
            price = _to_float(it.get("price"))
            if price is None:
                continue
            it["_price_num"] = price
            reasons = []
            if w.get("max_price") is not None and price <= w["max_price"]:
                reasons.append(f"低于¥{w['max_price']}")
            if w.get("min_price") is not None and price >= w["min_price"]:
                reasons.append(f"高于¥{w['min_price']}")
            if reasons:
                reason = "+".join(reasons)
                db.record_alert(w["id"], it, reason)
                alerts.append({"title": it.get("title", "")[:60], "price": price, "reason": reason})

        results.append({
            "watch_id": w["id"],
            "keyword": w["keyword"],
            "captured": len(items),
            "alerts": alerts,
        })
    return {"results": results, "ran_at": time.strftime("%Y-%m-%d %H:%M:%S")}
