"""SQLite 持久层 — 价格监控历史 + 告警记录。零依赖。"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional


class WatchDB:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._conn() as conn:
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

    # ---- watch items ----
    def add_watch(self, keyword: str, max_price: float | None = None, min_price: float | None = None) -> int:
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
                   ORDER BY checked_at DESC LIMIT ?""",
                (watch_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def latest_per_item(self, watch_id: int) -> list[dict[str, Any]]:
        """每个商品的最新一条记录（用于去重告警）。"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT h.* FROM price_history h
                   JOIN (SELECT item_id, MAX(checked_at) m FROM price_history
                         WHERE watch_id=? GROUP BY item_id) x
                     ON h.item_id=x.item_id AND h.checked_at=x.m
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

    def recent_alerts(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]


def _to_float(v: Any) -> float | None:
    """'¥180' → 180.0；'包邮' → None。"""
    if v is None:
        return None
    s = str(v).replace("¥", "").replace("￥", "").strip()
    try:
        return round(float(s), 2)
    except ValueError:
        return None
