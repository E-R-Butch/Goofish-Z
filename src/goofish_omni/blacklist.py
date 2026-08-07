"""黑名单/屏蔽规则 — 过滤劣质商家，优化检索与监控结果。

规则维度（基于搜索结果现有字段）：
1. title_keywords  — 标题含这些词就屏蔽（如 代拍/勿拍/引流）。支持 `re:` 前缀正则
2. location_black — 地区黑名单（骗子高发区）
3. no_badge       — 屏蔽无信用标识的（badge 为空）
4. price_drop     — 屏蔽"累计降价 N%"且 N >= 阈值的（反复降价信号）
5. seller_nick    — 屏蔽指定卖家昵称（劣质商家 ID 库）

正则与 token 匹配逻辑整合自 ai-goofish-monitor 的 result_blacklist_service：
- `re:` 前缀 → 正则匹配
- 纯 ASCII 关键词（如 "refurb"）→ token 边界匹配，避免 "apple" 误伤 "pineapple"
- 中文关键词 → 子串包含匹配（中文无词边界）
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

_REGEX_PREFIX = "re:"
_ASCII_TOKEN_PATTERN = re.compile(r"^[a-z0-9 ]+$")
_ASCII_BOUNDARY = r"[a-z0-9]"


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
        """过滤商品。返回 (通过, 被屏蔽)。

        批内分析：先计算同批价格中位数，供 price_anomaly 规则判断
        "低价引流"（价格显著低于同类）。
        """
        rules = [r for r in self.list_rules() if r["enabled"]]
        if not rules:
            return items, []

        # 批内价格统计：优先用每 GB 单价（元/GB），无容量信息的商品退回裸价。
        # 原因：16G ¥150 vs 32G ¥200 裸价看似 16G 便宜，实际 16G ¥9.4/GB 比
        # 32G ¥6.25/GB 贵——容量归一化才可比。
        unit_prices = [_unit_price(it) for it in items]
        unit_prices = [p for p in unit_prices if p is not None]
        raw_prices = [_to_float(it.get("price")) for it in items]
        raw_prices = [p for p in raw_prices if p is not None]
        median_unit = _median(unit_prices) if unit_prices else None
        median_raw = _median(raw_prices) if raw_prices else None

        passed: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []

        for it in items:
            # 低价标记（不屏蔽！）：显著低于同类中位价 → 可能是捡漏，也可能是引流。
            # 作为 _price_flag 附带在商品上，由调用方决定如何呈现——绝不自动屏蔽。
            # 有容量的商品比每GB单价（更准），无容量的退回裸价比。
            unit = _unit_price(it)
            if unit is not None and median_unit and median_unit > 0:
                ratio = unit / median_unit
                if ratio < 0.5:
                    it["_price_flag"] = f"低价(同类每GB ¥{median_unit:.2f}的{ratio*100:.0f}%)"
                elif ratio < 0.8:
                    it["_price_flag"] = f"偏低(同类每GB ¥{median_unit:.2f}的{ratio*100:.0f}%)"
            else:
                price = _to_float(it.get("price"))
                if median_raw is not None and price is not None and median_raw > 0:
                    ratio = price / median_raw
                    if ratio < 0.5:
                        it["_price_flag"] = f"低价(中位价¥{median_raw:.0f}的{ratio*100:.0f}%)"
                    elif ratio < 0.8:
                        it["_price_flag"] = f"偏低(中位价¥{median_raw:.0f}的{ratio*100:.0f}%)"

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


def _to_float(v: Any) -> float | None:
    """'¥180' → 180.0；'包邮' → None。"""
    if v is None:
        return None
    s_val = str(v).replace("¥", "").replace("￥", "").strip()
    try:
        return round(float(s_val), 2)
    except ValueError:
        return None


_CAPACITY_RE = re.compile(r"(\d{1,3})\s*(?:GB|G)\b", re.IGNORECASE)


def _extract_capacity(title: str) -> int | None:
    """从标题提取容量（GB）。16G/32G/64G → 16/32/64。"""
    m = _CAPACITY_RE.search(str(title or ""))
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def capacity_matches(title: str, required_cap: int | None) -> bool:
    """校验商品容量是否匹配搜索要求。

    required_cap=32 时：
    - 标题含 32G/32GB → 通过
    - 标题含 16G 但不含 32 → 拒绝（污染）
    - 标题同时含 16G+32G（如"16GB内存条 32G套装"）→ 通过（套装可能相关）
    - 标题无容量信息 → 通过（无法判断，不误杀）
    """
    if required_cap is None:
        return True
    caps = set()
    for m in re.finditer(r"(\d{1,3})\s*(?:GB|G)\b", str(title or ""), re.IGNORECASE):
        try:
            caps.add(int(m.group(1)))
        except ValueError:
            pass
    if not caps:
        return True  # 无容量信息，不误杀
    if required_cap in caps:
        return True  # 含目标容量
    return False  # 有容量但不含目标 → 污染


_GENERATION_RE = re.compile(r"(DDR\d)", re.IGNORECASE)


def extract_generation(title: str) -> str | None:
    """从标题提取内存代数（DDR3/DDR4/DDR5）。无 → None。"""
    m = _GENERATION_RE.search(str(title or ""))
    if not m:
        return None
    return m.group(1).upper()


def is_broken_stick(item: dict[str, Any], max_price: float = 50) -> bool:
    """坏条/报废条特例：代数匹配但标为坏条/报废/坏料/收藏，且价格极低。

    练手/拆件价值的坏条可以保留（如 DDR3 坏条 ¥10）。
    """
    title = str(item.get("title", ""))
    if not any(w in title for w in ("坏条", "报废", "坏料", "收藏摆件", "残次", "点不亮")):
        return False
    price = _to_float(item.get("price"))
    return price is not None and price <= max_price


def _unit_price(it: dict[str, Any]) -> float | None:
    """每 GB 价格（元/GB）。无容量或无法解析价格 → None。"""
    price = _to_float(it.get("price"))
    cap = _extract_capacity(str(it.get("title", "")))
    if price is None or not cap or cap <= 0:
        return None
    return price / cap


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    srt = sorted(values)
    n = len(srt)
    mid = n // 2
    if n % 2 == 0:
        return (srt[mid - 1] + srt[mid]) / 2
    return srt[mid]


def _is_regex_keyword(keyword: str) -> bool:
    return keyword.lower().startswith(_REGEX_PREFIX)


def _uses_ascii_token_match(keyword: str) -> bool:
    return bool(keyword) and _ASCII_TOKEN_PATTERN.fullmatch(keyword) is not None


def _keyword_matches(keyword: str, text: str) -> bool:
    """关键词匹配：`re:` 正则 / 纯 ASCII token 边界 / 中文子串。"""
    if _is_regex_keyword(keyword):
        pattern = keyword[len(_REGEX_PREFIX):]
        try:
            return re.search(pattern, text, flags=re.IGNORECASE) is not None
        except re.error:
            return False
    if not _uses_ascii_token_match(keyword):
        return keyword in text
    pattern = rf"(?<!{_ASCII_BOUNDARY}){re.escape(keyword)}(?!{_ASCII_BOUNDARY})"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _check_item(item: dict[str, Any], rules: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    title = str(item.get("title", ""))
    location = str(item.get("location", ""))
    badge = str(item.get("badge", "") or "")
    orig = str(item.get("original_price", "") or "")
    seller_nick = str(item.get("seller_nick", "") or "")

    for r in rules:
        kind, value = r["kind"], str(r["value"])
        if not r["enabled"]:
            continue
        if kind == "title_keyword":
            if value and _keyword_matches(value, title):
                reasons.append(f"标题命中「{value}」")
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
        elif kind == "seller_nick":
            # 卖家昵称精确/包含匹配。注意闲鱼详情页可能显示 tbNick_xxx 脱敏昵称，
            # 真实昵称从 mtop detail API 的 seller_nick 字段拿。
            if value and (seller_nick == value or value in seller_nick):
                reasons.append(f"劣质商家「{value}」")
    return reasons
