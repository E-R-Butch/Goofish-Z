"""黑名单/屏蔽规则 — 过滤劣质商家，优化检索与监控结果。

规则维度（基于搜索结果现有字段）：
1. title_keywords  — 标题含这些词就屏蔽（如 代拍/勿拍/引流）
2. location_black — 地区黑名单（骗子高发区）
3. no_badge       — 屏蔽无信用标识的（badge 为空）
4. price_drop     — 屏蔽"累计降价 N%"且 N >= 阈值的（反复降价信号）
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any


class BlacklistDB:
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
                """CREATE TABLE IF NOT EXISTS blacklist_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,          -- title_keyword / location / no_badge / price_drop
                    value TEXT NOT NULL,         -- 关键词 / 地区 / 阈值
                    note TEXT DEFAULT '',
                    enabled INTEGER DEFAULT 1,
                    created_at INTEGER
                )"""
            )

    # ---- 规则 CRUD ----
    def add_rule(self, kind: str, value: str, note: str = "") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO blacklist_rules (kind, value, note, created_at) VALUES (?,?,?,?)",
                (kind, value, note, int(time.time())),
            )
            return cur.lastrowid

    def list_rules(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM blacklist_rules ORDER BY id").fetchall()
            return [dict(r) for r in rows]

    def remove_rule(self, rule_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM blacklist_rules WHERE id=?", (rule_id,))
            return cur.rowcount > 0

    def set_enabled(self, rule_id: int, enabled: bool) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE blacklist_rules SET enabled=? WHERE id=?", (1 if enabled else 0, rule_id))

    # ---- 过滤执行 ----
    def filter_items(self, items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """过滤商品。返回 (通过, 被屏蔽)。"""
        rules = [r for r in self.list_rules() if r["enabled"]]
        if not rules:
            return items, []

        passed: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []

        for it in items:
            reasons = _check_item(it, rules)
            if reasons:
                it["_blocked_reasons"] = reasons
                blocked.append(it)
            else:
                passed.append(it)
        return passed, blocked

    def explain(self, item: dict[str, Any]) -> list[str]:
        """解释单条商品为什么被屏蔽（调试用）。"""
        return list(item.get("_blocked_reasons", []))


def _check_item(item: dict[str, Any], rules: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    title = str(item.get("title", ""))
    location = str(item.get("location", ""))
    badge = str(item.get("badge", "") or "")
    orig = str(item.get("original_price", "") or "")

    for r in rules:
        kind, value = r["kind"], str(r["value"])
        if not r["enabled"]:
            continue
        if kind == "title_keyword":
            if value and value in title:
                reasons.append(f"标题含「{value}」")
        elif kind == "location":
            if value and value in location:
                reasons.append(f"地区 {location}")
        elif kind == "no_badge" and value == "1":
            if not badge:
                reasons.append("无信用标识")
        elif kind == "price_drop":
            # original_price 形如 "累计降价14%" → 提取数字
            m = re.search(r"累计降价(\d+)%", orig)
            if m and int(m.group(1)) >= int(value):
                reasons.append(f"累计降价{m.group(1)}%")
    return reasons
