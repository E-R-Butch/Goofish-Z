"""SQLite 持久层 — 价格监控历史 + 告警记录。零依赖。"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Optional


class WatchDB:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def _conn(self):
        with closing(sqlite3.connect(self.db_path, timeout=30)) as conn:
            conn.row_factory = sqlite3.Row
            with conn:
                yield conn

    def _init(self) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS watch_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT NOT NULL,
                    max_price REAL,           -- 低于此价才告警，NULL=不限
                    min_price REAL,           -- 高于此价才告警，NULL=不限
                    enabled INTEGER DEFAULT 1,
                    created_at INTEGER,
                    last_check_at INTEGER
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    watch_id INTEGER,
                    item_id TEXT,
                    title TEXT,
                    price REAL,
                    location TEXT,
                    url TEXT,
                    raw TEXT,
                    checked_at INTEGER
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    watch_id INTEGER,
                    item_id TEXT,
                    title TEXT,
                    price REAL,
                    reason TEXT,
                    created_at INTEGER
                )"""
            )

            columns = {row["name"] for row in conn.execute("PRAGMA table_info(alerts)")}
            for name, declaration in (("read_at", "INTEGER"), ("url", "TEXT DEFAULT ''")):
                if name not in columns:
                    conn.execute(f"ALTER TABLE alerts ADD COLUMN {name} {declaration}")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS alert_state (
                    watch_id INTEGER, item_id TEXT, price REAL, reason TEXT,
                    PRIMARY KEY (watch_id, item_id)
                )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS history_item ON price_history(watch_id, item_id, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS alerts_watch ON alerts(watch_id, id)")

    # ---- watch items ----
    def add_watch(self, keyword: str, max_price: float | None = None, min_price: float | None = None) -> int:
        keyword = keyword.strip()
        if not keyword:
            raise ValueError("监控关键词不能为空")
        for price in (max_price, min_price):
            if price is not None and (not math.isfinite(price) or price < 0):
                raise ValueError("告警价格必须为非负有限数值")
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO watch_items (keyword, max_price, min_price, created_at) VALUES (?,?,?,?)",
                (keyword, max_price, min_price, int(time.time())),
            )
            return cur.lastrowid

    def list_watches(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM watch_items ORDER BY id").fetchall()
            return [dict(r) for r in rows]

    def get_watch(self, watch_id: int) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            r = conn.execute("SELECT * FROM watch_items WHERE id=?", (watch_id,)).fetchone()
            return dict(r) if r else None

    def set_watch_enabled(self, watch_id: int, enabled: bool) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE watch_items SET enabled=? WHERE id=?", (1 if enabled else 0, watch_id))

    def remove_watch(self, watch_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM watch_items WHERE id=?", (watch_id,))
            conn.execute("DELETE FROM price_history WHERE watch_id=?", (watch_id,))
            conn.execute("DELETE FROM alerts WHERE watch_id=?", (watch_id,))
            conn.execute("DELETE FROM alert_state WHERE watch_id=?", (watch_id,))
            return cur.rowcount > 0

    def touch_check(self, watch_id: int) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE watch_items SET last_check_at=? WHERE id=?", (int(time.time()), watch_id))

    # ---- price history ----
    def record_items(self, watch_id: int, items: list[dict[str, Any]]) -> None:
        """落盘一次搜索结果。返回 (watch_id, items) 由调用方决定告警。"""
        now = int(time.time())
        with self._conn() as conn:
            conn.executemany(
                """INSERT INTO price_history
                   (watch_id, item_id, title, price, location, url, raw, checked_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [
                    (
                        watch_id,
                        str(i.get("item_id", "")),
                        i.get("title", "")[:200],
                        _to_float(i.get("price")),
                        i.get("location", ""),
                        i.get("url", ""),
                        json.dumps(i, ensure_ascii=False)[:2000],
                        now,
                    )
                    for i in items
                ],
            )

    def history(self, watch_id: int, limit: int = 200) -> list[dict[str, Any]]:
        """按 item_id 聚合的最新价格历史（用于画曲线）。"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT item_id, title, price, checked_at, url, location
                   FROM price_history WHERE watch_id=?
                   ORDER BY checked_at DESC, id DESC LIMIT ?""",
                (watch_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def latest_per_item(self, watch_id: int) -> list[dict[str, Any]]:
        """每个商品的最新一条记录（用于去重告警）。"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT h.* FROM price_history h
                   JOIN (SELECT item_id, MAX(id) m FROM price_history
                         WHERE watch_id=? GROUP BY item_id) x
                     ON h.id=x.m
                   WHERE h.watch_id=? ORDER BY h.price""",
                (watch_id, watch_id),
            ).fetchall()
            return [dict(r) for r in rows]

    # ---- alerts ----
    def record_alert(self, watch_id: int, item: dict[str, Any], reason: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO alerts (watch_id, item_id, title, price, reason, created_at) VALUES (?,?,?,?,?,?)",
                (watch_id, str(item.get("item_id", "")), item.get("title", "")[:200],
                 _to_float(item.get("price")), reason, int(time.time())),
            )

    def recent_alerts(self, limit: int = 20, unread_only: bool = False) -> list[dict[str, Any]]:
        with self._conn() as conn:
            where = "WHERE read_at IS NULL" if unread_only else ""
            rows = conn.execute(
                f"SELECT * FROM alerts {where} ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_alert_read(self, alert_id: int) -> bool:
        with self._conn() as conn:
            result = conn.execute(
                "UPDATE alerts SET read_at=COALESCE(read_at, ?) WHERE id=?",
                (int(time.time()), alert_id),
            )
            return result.rowcount > 0

    def record_poll(self, watch_id: int, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Commit observations and threshold transitions together, including deduplication."""
        from goofish_z.core.errors import NotFoundError

        now = int(time.time())
        alerts = []
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            watch = conn.execute("SELECT * FROM watch_items WHERE id=?", (watch_id,)).fetchone()
            if watch is None:
                raise NotFoundError("监控项已删除")
            for item in items:
                item_id = str(item.get("item_id", ""))
                price = _to_float(item.get("price"))
                if item_id and price is not None:
                    reason = _alert_reason(watch, price)
                    previous = conn.execute(
                        "SELECT price, reason FROM alert_state WHERE watch_id=? AND item_id=?",
                        (watch_id, item_id),
                    ).fetchone()
                    if previous is None:
                        # Seed existing installations from the last real observation.
                        old = conn.execute(
                            "SELECT price FROM price_history WHERE watch_id=? AND item_id=? ORDER BY id DESC LIMIT 1",
                            (watch_id, item_id),
                        ).fetchone()
                        if old and old["price"] is not None:
                            previous = {"price": old["price"], "reason": _alert_reason(watch, old["price"])}
                    if reason and (previous is None or previous["price"] != price or previous["reason"] != reason):
                        cursor = conn.execute(
                            """INSERT INTO alerts (watch_id,item_id,title,price,reason,created_at,url)
                               VALUES (?,?,?,?,?,?,?)""",
                            (watch_id, item_id, item.get("title", "")[:200], price, reason, now, item.get("url", "")),
                        )
                        alerts.append({"id": cursor.lastrowid, "item_id": item_id,
                                       "title": item.get("title", "")[:200], "price": price,
                                       "reason": reason, "url": item.get("url", "")})
                    conn.execute(
                        """INSERT INTO alert_state (watch_id,item_id,price,reason) VALUES (?,?,?,?)
                           ON CONFLICT(watch_id,item_id) DO UPDATE SET price=excluded.price, reason=excluded.reason""",
                        (watch_id, item_id, price, reason),
                    )
                conn.execute(
                    """INSERT INTO price_history (watch_id,item_id,title,price,location,url,raw,checked_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (watch_id, item_id, item.get("title", "")[:200], price,
                     item.get("location", ""), item.get("url", ""),
                     json.dumps(item, ensure_ascii=False), now),
                )
            conn.execute("UPDATE watch_items SET last_check_at=? WHERE id=?", (now, watch_id))
        return alerts


def _alert_reason(watch: Any, price: float) -> str:
    reasons = []
    if watch["max_price"] is not None and price <= watch["max_price"]:
        reasons.append(f"低于或等于¥{watch['max_price']}")
    if watch["min_price"] is not None and price >= watch["min_price"]:
        reasons.append(f"高于或等于¥{watch['min_price']}")
    return "+".join(reasons)


def _to_float(v: Any) -> float | None:
    """'¥180' → 180.0；'包邮' → None。"""
    if v is None:
        return None
    s = str(v).replace("¥", "").replace("￥", "").strip()
    try:
        value = float(s)
        return round(value, 2) if math.isfinite(value) and value >= 0 else None
    except ValueError:
        return None
