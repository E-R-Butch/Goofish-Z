"""watch — 价格监控（生态空白点）。定时搜索 + SQLite 落盘 + 告警。"""
from __future__ import annotations

import time
from typing import Any

from loguru import logger

from goofish_z.core.paths import runtime_data_path
from goofish_z.core.registry import command
from goofish_z.db import WatchDB

DEFAULT_DB = runtime_data_path("watch.db")


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
    """执行监控，限流时等待；HTTP 后台任务复用相同实现。"""
    return run_watches(watch_id=watch_id, all=all, limit=limit, enrich_sellers=enrich_sellers)


def run_watches(watch_id=None, all=False, limit=20, enrich_sellers=False, *, progress=None, cancel=None):
    from goofish_z.commands.search.search import search as search_cmd
    from goofish_z.blacklist import BlacklistDB
    from goofish_z.signals import SellerSignalDB
    from goofish_z.core.errors import AuthRequiredError, NotFoundError, RateLimitedError, RiskControlError
    from goofish_z.core.guard import check as guard_check

    if bool(all) == (watch_id is not None):
        raise ValueError("需要且只能指定 watch_id 或 --all 其中一个")
    if not 1 <= limit <= 50:
        raise ValueError("limit 必须在 1 到 50 之间")
    db = _db()
    if all:
        targets = [w for w in db.list_watches() if w["enabled"]]
    else:
        target = db.get_watch(watch_id)
        if not target:
            raise NotFoundError("监控项不存在")
        targets = [target]
    results = []
    bdb = BlacklistDB(DEFAULT_DB)
    sdb = SellerSignalDB(DEFAULT_DB)
    stopped = None

    def report(phase, **fields):
        if progress:
            progress({"phase": phase, "completed": len(results), "total": len(targets), **fields})

    def cancelled():
        return cancel is not None and cancel.is_set()

    report("running")
    for w in targets:
        if cancelled():
            stopped = "cancelled"
            break
        report("searching", keyword=w["keyword"], watch_id=w["id"])
        try:
            while True:
                guard_check()
                if cancelled():
                    stopped = "cancelled"
                    break
                try:
                    items = search_cmd(str(w["keyword"]), limit=limit, filter_blacklist=False).get("items", [])
                    break
                except RateLimitedError as exc:
                    delay = max(0.1, exc.retry_after or 0.1) + 0.05
                    report("waiting", keyword=w["keyword"], retry_after=delay)
                    if cancel is not None:
                        cancel.wait(delay)
                    else:
                        time.sleep(delay)
            if stopped or cancelled():
                stopped = "cancelled"
                break
            if enrich_sellers:
                items = _enrich_seller_nicks(items, w)
            if cancelled():
                stopped = "cancelled"
                break
            # Update signals before filtering so a newly banned seller cannot alert this poll.
            auto_banned = _apply_signal_engine(sdb, items)
            passed, blocked = bdb.filter_items(items)
            alerts = db.record_poll(w["id"], passed)
            bargains = [
                {"title": it.get("title", "")[:50], "price": it.get("price"), "flag": it["_price_flag"]}
                for it in passed if it.get("_price_flag")
            ]
            results.append({
                "watch_id": w["id"], "keyword": w["keyword"], "status": "succeeded",
                "captured": len(passed), "blocked_count": len(blocked), "auto_banned": auto_banned,
                "bargain_count": len(bargains), "bargains": bargains[:10], "alerts": alerts,
                "blocked": [{"title": it.get("title", "")[:50], "price": it.get("price"),
                             "reasons": it.get("_blocked_reasons", [])} for it in blocked[:10]],
            })
        except Exception as exc:
            results.append({"watch_id": w["id"], "keyword": w["keyword"], "status": "failed", "error": str(exc)})
            if isinstance(exc, (RiskControlError, AuthRequiredError)):
                stopped = "failed"
                break
        report("running")
    if stopped:
        for w in targets[len(results):]:
            results.append({"watch_id": w["id"], "keyword": w["keyword"], "status": "skipped",
                            "error": "已取消" if stopped == "cancelled" else "登录或风控异常，本轮已停止"})
    succeeded = sum(r["status"] == "succeeded" for r in results)
    failed = sum(r["status"] == "failed" for r in results)
    skipped = sum(r["status"] == "skipped" for r in results)
    status = "cancelled" if stopped == "cancelled" else (
        "partial" if succeeded and (failed or skipped) else "failed" if failed or skipped else "succeeded"
    )
    report(status)
    return {"status": status, "results": results, "succeeded": succeeded, "failed": failed,
            "skipped": skipped, "ran_at": time.strftime("%Y-%m-%d %H:%M:%S")}


@command(namespace="watch", name="alerts", description="读取实际告警记录", columns=["id", "title", "price", "reason", "read_at"])
def watch_alerts(limit: int = 50, unread_only: bool = False) -> dict[str, Any]:
    return {"alerts": _db().recent_alerts(limit=max(1, min(limit, 200)), unread_only=unread_only)}


@command(namespace="watch", name="read-alert", description="将告警标为已读", columns=["alert_id", "read"])
def watch_read_alert(alert_id: int) -> dict[str, Any]:
    from goofish_z.core.errors import NotFoundError
    if not _db().mark_alert_read(alert_id):
        raise NotFoundError("告警不存在")
    return {"alert_id": alert_id, "read": True}


def _enrich_seller_nicks(items: list[dict[str, Any]], watch: dict[str, Any]) -> list[dict[str, Any]]:
    """对搜索结果逐个补查卖家昵称（detail API → seller_nick）。

    仅在存在启用的 seller_nick 规则时执行，避免无谓的 API 请求。
    每个商品间隔 1.5s，降低触发风控的概率。
    """
    from goofish_z.blacklist import BlacklistDB
    from goofish_z.commands.item.view import view as item_view
    from goofish_z.core.errors import AuthRequiredError, GoofishError, RiskControlError

    bdb = BlacklistDB(DEFAULT_DB)
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
            except (RiskControlError, AuthRequiredError):
                raise
            except GoofishError as e:
                # 非登录/风控异常：跳过该条补查，保留原数据
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

    from goofish_z.core.errors import AuthRequiredError, RiskControlError
    from goofish_z.core.browser import goofish_page

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
        from goofish_z.core.limiter import check as rate_check

        rate_check("seller_page")
        url = f"https://www.goofish.com/item?id={item_id}"
        async with goofish_page() as page:
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            return await page.evaluate(JS, item_id) or ""

    try:
        return asyncio.run(_run())
    except (AuthRequiredError, RiskControlError):
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[seller] 页面抓取卖家昵称失败 {item_id}: {e}")
        return ""

def _apply_signal_engine(sdb, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """信号引擎：检测信号 → 按卖家聚合 → 自动拉黑。

    返回带 _signals 标注的商品列表（供结果展示）。
    只对有 seller_nick 的商品做卖家聚合（无昵称的仅标注，不聚合）。
    """
    # 批内价格统计：每GB单价中位数（有容量）+ 裸价中位数（无容量兜底）
    from goofish_z.blacklist import _extract_capacity, _median, _to_float
    from goofish_z.signals import detect_signals

    unit_prices = []
    raw_prices = []
    for it in items:
        price = _to_float(it.get("price"))
        if price is None:
            continue
        raw_prices.append(price)
        cap = _extract_capacity(str(it.get("title", "")))
        if cap and cap > 0:
            unit_prices.append(price / cap)
    median_unit = _median(unit_prices) if unit_prices else None
    median_raw = _median(raw_prices) if raw_prices else None

    auto_banned: list[dict[str, Any]] = []
    for it in items:
        sigs = detect_signals(it, median_unit, median_raw)
        it["_signals"] = sigs
        if not sigs:
            continue
        nick = str(it.get("seller_nick", "") or "")
        if nick:
            sdb.record_signals(nick, str(it.get("item_id", "")), str(it.get("title", "")), sigs)
            profile = sdb.get_profile(nick)
            if profile and profile.get("auto_banned"):
                auto_banned.append({"seller": nick, "score": profile.get("total_score"),
                                    "signals": sigs})
    return auto_banned
