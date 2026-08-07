"""watch — 价格监控（生态空白点）。定时搜索 + SQLite 落盘 + 告警。"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from loguru import logger

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
def watch_run(watch_id: int | None = None, all: bool = False, limit: int = 20,
                  enrich_sellers: bool = False) -> dict[str, Any]:
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
    from goofish_omni.core.guard import check as guard_check

    bdb = BlacklistDB(Path.home() / ".goofish-omni" / "watch.db")
    for w in targets:
        # 熔断检查：一旦触发风控熔断，停止后续所有监控项（不硬闯）
        try:
            guard_check()
        except Exception as e:
            results.append({"watch_id": w["id"], "keyword": w["keyword"],
                            "error": f"风控熔断，停止本轮: {str(e)[:80]}"})
            break

        try:
            items = search_cmd(str(w["keyword"]), limit=limit).get("items", [])
        except Exception as e:
            results.append({"watch_id": w["id"], "keyword": w["keyword"], "error": str(e)})
            continue

        # 层级1优先：默认只用搜索列表字段（价格/地区/badge/标题/降价）过滤。
        # 卖家昵称补查（层级2/3，有风控代价）仅当显式 --enrich-sellers 才执行。
        if enrich_sellers:
            items = _enrich_seller_nicks(items, w)

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

        # 低价捡漏候选：通过黑名单且被标记低价的商品（不屏蔽，重点提示）
        bargains = [
            {"title": it.get("title", "")[:50], "price": it.get("price"),
             "flag": it.get("_price_flag", "")}
            for it in passed if it.get("_price_flag")
        ]

        results.append({
            "watch_id": w["id"],
            "keyword": w["keyword"],
            "captured": len(passed),
            "blocked_count": len(blocked),
            "bargain_count": len(bargains),
            "bargains": bargains[:10],
            "blocked": [
                {"title": b.get("title", "")[:50], "price": b.get("price"),
                 "reasons": b.get("_blocked_reasons", [])}
                for b in blocked[:10]
            ],
            "alerts": alerts,
        })
    return {"results": results, "ran_at": time.strftime("%Y-%m-%d %H:%M:%S")}


def _enrich_seller_nicks(items: list[dict[str, Any]], watch: dict[str, Any]) -> list[dict[str, Any]]:
    """对搜索结果逐个补查卖家昵称（detail API → seller_nick）。

    仅在存在启用的 seller_nick 规则时执行，避免无谓的 API 请求。
    每个商品间隔 1.5s，降低触发风控的概率。
    """
    from goofish_omni.blacklist import BlacklistDB
    from goofish_omni.commands.item.view import view as item_view
    from goofish_omni.core.errors import GoofishError

    bdb = BlacklistDB(Path.home() / ".goofish-omni" / "watch.db")
    has_seller_rule = any(
        r["kind"] == "seller_nick" and r["enabled"]
        for r in bdb.list_rules()
    )
    if not has_seller_rule or not items:
        return items

    import time

    enriched = []
    for it in items:
        item_id = str(it.get("item_id", ""))
        if item_id:
            try:
                nick = _fetch_seller_nick_via_page(item_id)
                if nick:
                    it["seller_nick"] = nick
            except GoofishError as e:
                # 风控/过期：跳过该条补查，保留原数据
                it.setdefault("_seller_lookup_error", str(e)[:80])
            except Exception:
                pass
            time.sleep(1.5)
        enriched.append(it)
    return enriched


def _fetch_seller_nick_via_page(item_id: str) -> str:
    """轻量抓商品详情页文本，解析卖家昵称。

    闲鱼详情页的卖家区块结构：昵称行紧跟信用等级行（如「南山科技 / 卖家信用极好」）。
    比 item view（等页面内 mtop 就绪）快且稳，也不触发外部 detail API 风控。
    """
    import asyncio

    from goofish_omni.core.browser import goofish_page

    JS = """
(itemId) => {
  const body = document.body.innerText || '';
  const lines = body.split('\\n').map(s => s.trim()).filter(Boolean);
  // 卖家昵称行特征：在「卖家信用*」行的上一行，且不含 ¥ / 想要 / 商品词
  const creditIdx = lines.findIndex(l => l.startsWith('卖家信用'));
  if (creditIdx < 0) return '';
  const nick = lines[creditIdx - 1] || '';
  // 过滤掉明显不是昵称的（价格、数量词）
  if (!nick || /^[¥¥0-9]/.test(nick) || nick.includes('想要')) return '';
  return nick;
}
"""

    async def _run() -> str:
        # 限流：页面抓取间隔 4s
        from goofish_omni.core.limiter import check as rate_check

        rate_check("seller_page")
        url = f"https://www.goofish.com/item?id={item_id}"
        async with goofish_page() as page:
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            return await page.evaluate(JS, item_id) or ""

    try:
        return asyncio.run(_run())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[seller] 页面抓取卖家昵称失败 {item_id}: {e}")
        return ""
