"""自动黑名单信号引擎 — 卖家信号档案 + 标签叠加自动拉黑。

设计：
1. 每次 watch run / search 给商品打信号标签（_signals 字段）
2. 按卖家昵称聚合 → 卖家档案（累计信号分 + 出现次数）
3. 分数 ≥ AUTO_BAN_THRESHOLD → 自动拉黑（source=auto）
4. 手动拉黑（source=manual）= 硬证据，永不自动解除

信号权重（硬证据 > 叠加信号）：
- 手动拉黑(北冥有鱼)         = 硬证据，直接屏蔽
- 低价引流(<中位价50%)      +3  ← 挂低价不卖的核心信号
- 无信用标识                +2
- 标题含引流词(代拍/勿拍等)  +2
- 累计降价≥30%              +1
- 地区黑名单                +2
- 多次出现且持续低价         +1/次
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

# 自动拉黑阈值：卖家累计信号分达到即拉黑
AUTO_BAN_THRESHOLD = 5
# 同卖家出现 N 次以上才纳入自动评分（防单次误判）
MIN_APPEARANCES = 2

# 信号 → 分值
SIGNAL_WEIGHTS: dict[str, int] = {
    "low_price_trap": 3,     # 低价引流（显著低于同类）
    "no_badge": 2,           # 无信用标识
    "title_keyword": 2,      # 标题含引流词
    "price_drop": 1,         # 反复降价
    "bad_location": 2,       # 地区黑名单
}

# 标题引流词（自动识别，无需手动配置）
TITLE_TRAP_WORDS = ("代拍", "勿拍", "引流", "加V", "加微", "私聊优惠", "VX", "微信")


class SellerSignalDB:
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
                """CREATE TABLE IF NOT EXISTS seller_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    seller_nick TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    weight INTEGER NOT NULL,
                    item_id TEXT DEFAULT '',
                    title TEXT DEFAULT '',
                    created_at INTEGER,
                    UNIQUE(seller_nick, signal, item_id)
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS seller_profiles (
                    seller_nick TEXT PRIMARY KEY,
                    total_score INTEGER DEFAULT 0,
                    appearances INTEGER DEFAULT 0,
                    last_seen_at INTEGER,
                    auto_banned INTEGER DEFAULT 0,
                    banned_at INTEGER,
                    signals_json TEXT DEFAULT '{}'
                )"""
            )

    # ---- 信号记录 ----
    def record_signals(self, seller_nick: str, item_id: str, title: str,
                       signals: list[str]) -> None:
        """记录一个商品对某卖家的信号。"""
        if not seller_nick:
            return
        now = int(time.time())
        with self._conn() as conn:
            for sig in set(signals):
                weight = SIGNAL_WEIGHTS.get(sig, 0)
                if weight <= 0:
                    continue
                conn.execute(
                    """INSERT OR IGNORE INTO seller_signals
                       (seller_nick, signal, weight, item_id, title, created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (seller_nick, sig, weight, item_id, title[:200], now),
                )
            self._refresh_profile(conn, seller_nick)

    def _refresh_profile(self, conn: sqlite3.Connection, seller_nick: str) -> None:
        """重算卖家档案（分数/次数/信号分布）。

        分数 = 各信号权重 × 出现商品数（重复信号跨商品叠加）。
        """
        row = conn.execute(
            """SELECT SUM(weight) as score,
                      COUNT(DISTINCT item_id) as apps
               FROM seller_signals WHERE seller_nick=?""",
            (seller_nick,),
        ).fetchone()
        sig_rows = conn.execute(
            "SELECT signal, COUNT(*) as n FROM seller_signals WHERE seller_nick=? GROUP BY signal",
            (seller_nick,),
        ).fetchall()
        signals_json = json.dumps(
            {r["signal"]: r["n"] for r in sig_rows}, ensure_ascii=False
        )
        conn.execute(
            """INSERT INTO seller_profiles
               (seller_nick, total_score, appearances, last_seen_at, signals_json)
               VALUES (?,?,?,?,?)
               ON CONFLICT(seller_nick) DO UPDATE SET
                 total_score=excluded.total_score,
                 appearances=excluded.appearances,
                 last_seen_at=excluded.last_seen_at,
                 signals_json=excluded.signals_json""",
            (seller_nick, row["score"] or 0, row["apps"] or 0, int(time.time()), signals_json),
        )
        # 自动拉黑判定：分数达阈值 + 出现≥2次
        if (row["score"] or 0) >= AUTO_BAN_THRESHOLD and (row["apps"] or 0) >= MIN_APPEARANCES:
            conn.execute(
                """UPDATE seller_profiles SET auto_banned=1, banned_at=?
                   WHERE seller_nick=? AND auto_banned=0""",
                (int(time.time()), seller_nick),
            )

    # ---- 查询 ----
    def get_profile(self, seller_nick: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            r = conn.execute("SELECT * FROM seller_profiles WHERE seller_nick=?", (seller_nick,)).fetchone()
            return dict(r) if r else None

    def list_profiles(self, only_banned: bool = False, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            sql = "SELECT * FROM seller_profiles"
            if only_banned:
                sql += " WHERE auto_banned=1"
            sql += " ORDER BY total_score DESC LIMIT ?"
            return [dict(r) for r in conn.execute(sql, (limit,)).fetchall()]

    def unban(self, seller_nick: str) -> None:
        """手动解除自动拉黑（误判恢复）。"""
        with self._conn() as conn:
            conn.execute(
                "UPDATE seller_profiles SET auto_banned=0, banned_at=NULL WHERE seller_nick=?",
                (seller_nick,),
            )
            conn.execute("DELETE FROM seller_signals WHERE seller_nick=?", (seller_nick,))


def detect_signals(item: dict[str, Any], median_price: float | None) -> list[str]:
    """从单条商品提取信号标签。纯层级1字段，零额外请求。"""
    signals: list[str] = []
    title = str(item.get("title", ""))
    badge = str(item.get("badge", "") or "")
    orig = str(item.get("original_price", "") or "")

    # 无信用标识
    if not badge:
        signals.append("no_badge")

    # 标题引流词
    for w in TITLE_TRAP_WORDS:
        if w.lower() in title.lower():
            signals.append("title_keyword")
            break

    # 反复降价
    import re
    m = re.search(r"累计降价(\d+)%", orig)
    if m and int(m.group(1)) >= 30:
        signals.append("price_drop")

    # 低价引流：显著低于同类中位价
    if median_price and median_price > 0:
        try:
            price = float(str(item.get("price", "")).replace("¥", "").replace("￥", "").strip())
            if price < median_price * 0.5:
                signals.append("low_price_trap")
        except ValueError:
            pass

    return signals
