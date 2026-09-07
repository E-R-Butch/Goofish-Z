"""Keep observed price, eligibility and specification uncertainty distinct."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Platform = Literal["taobao", "jd", "pdd"]
PLATFORMS = {"taobao": "淘宝／天猫", "jd": "京东", "pdd": "拼多多"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Quote(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    platform: Platform
    item_id: str
    url: str
    title: str
    shop: str = ""
    query: str
    collected_at: str
    price_text: str
    price_low: float | None = Field(default=None, ge=0)
    price_high: float | None = Field(default=None, ge=0)
    currency: Literal["CNY"] = "CNY"
    price_kind: Literal["listed", "conditional", "range", "unknown"]
    conditions: list[str] = Field(default_factory=list)
    specification: str = ""
    specification_confirmed: bool = False
    match: Literal["title_match", "uncertain", "mismatch"] = "uncertain"
    reasons: list[str] = Field(default_factory=list)
    source: Literal["browser_dom"] = "browser_dom"


_AMOUNT = r"\d+(?:\.\d+)?(?:万|千)?"
_PRICE = re.compile(rf"^[¥￥]?({_AMOUNT})(?:[-~～—至][¥￥]?({_AMOUNT}))?元?(起)?$")


def parse_price(text: str) -> tuple[float | None, float | None, bool]:
    """A standalone price node only; never scan titles or subtract coupons."""
    clean = re.sub(r"[\s,，]", "", unicodedata.normalize("NFKC", str(text)))
    match = _PRICE.fullmatch(clean)
    if not match:
        return None, None, False

    def amount(value: str) -> float:
        multiplier = 10000 if value.endswith("万") else 1000 if value.endswith("千") else 1
        return float(Decimal(value.rstrip("万千")) * multiplier)

    try:
        low = amount(match[1])
        high = amount(match[2]) if match[2] else None if match[3] else low
    except (InvalidOperation, OverflowError):
        return None, None, False
    if low <= 0 or low > 1e10 or high is not None and (high < low or high > 1e10):
        return None, None, False
    return low, high, bool(match[2] or match[3])


def match_title(query: str, title: str) -> tuple[str, list[str]]:
    from goofish_z.blacklist import _extract_capacity, extract_gpu_models

    def normalize(value):
        return re.sub(r"[\s\-_]+", "", unicodedata.normalize("NFKC", value).lower())

    requested = extract_gpu_models(query)
    actual = extract_gpu_models(title)
    reasons: list[str] = []
    if requested and actual and not requested.intersection(actual):
        return "mismatch", ["商品型号与搜索型号不同"]
    if requested and len(actual) > 1:
        reasons.append("标题包含多个型号，尚未确认标价对应的型号")
    capacity = _extract_capacity(query)
    capacities = set(re.findall(r"(?<!\d)(\d+)\s*(?:GB|G)(?![a-z])", title, re.I))
    if capacity and capacities and str(capacity) not in capacities:
        return "mismatch", ["标题容量与搜索容量不同"]
    if capacity and len(capacities) > 1:
        reasons.append("标题包含多个容量，尚未确认标价对应的容量")
    # Specific GPU queries should not silently become whole-PC/accessory quotes.
    if requested and not re.search(r"主机|整机|笔记本|散热|配件|水冷|支架", query):
        if re.search(r"台式.*主机|组装机|整套主机|笔记本|散热器|显卡支架|空盒|无核心", title):
            reasons.append("可能是整机或配件，需确认商品类型")
    remaining = query
    if requested:
        remaining = re.sub(r"(?:RTX\s*)?[345]0[0-9]0\s*(?:SUPER|TI|D|S)?", "", remaining, flags=re.I)
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", remaining.lower())
    haystack = normalize(title)
    if any(normalize(token) not in haystack for token in tokens) or requested and not actual:
        reasons.append("标题未完整匹配搜索条件")
    return ("uncertain", reasons) if reasons else ("title_match", [])


def make_quote(platform: Platform, raw: dict, query: str, collected_at: str) -> Quote:
    from .urls import product_url

    url, item_id = product_url(platform, raw.get("url", ""))
    title = str(raw.get("title", "")).strip()[:2000]
    if not title:
        raise ValueError("商品标题缺失")
    price_text = str(raw.get("price_text", ""))[:120]
    low, high, is_range = parse_price(price_text)
    context = str(raw.get("context", ""))[:3000]
    conditions = []
    for pattern, label in [
        (r"券后|用券|券满|满\d+减", "含优惠券条件，未领取或核实资格"),
        (r"补贴|国补", "补贴价，资格或地区条件待确认"),
        (r"首购|新人|新客", "新客或首购条件"),
        (r"会员|PLUS|88VIP", "会员价格或权益"),
        (r"拼单|拼团|多人团|单独购买", "拼单与单独购买价格需区分"),
        (r"定金|预售|尾款", "预售或定金，未确认全款"),
        (r"到手|到手价", "页面展示到手价，适用条件待确认"),
        (r"参考|咨询.*价|价格.*咨询|非实价|引流", "参考或询价，尚未确认实际售价"),
        (r"批发|批量|起批", "可能有起订数量条件"),
    ]:
        if re.search(pattern, context, re.I):
            conditions.append(label)
    if platform == "pdd" and not any("拼单" in c for c in conditions):
        conditions.append("拼多多列表价，未确认拼单及优惠资格")
    match, reasons = match_title(query, title) if query else ("uncertain", ["未指定比较条件"])
    return Quote(
        platform=platform, item_id=item_id, url=url, title=title, query=query,
        shop=str(raw.get("shop", ""))[:200], collected_at=collected_at,
        price_text=price_text, price_low=low, price_high=high,
        price_kind="unknown" if low is None else "range" if is_range else "conditional" if conditions else "listed",
        conditions=conditions, specification=str(raw.get("specification", ""))[:500],
        match=match, reasons=reasons,
    )


def analyze(quotes: list[Quote]) -> dict:
    groups = []
    for platform in PLATFORMS:
        for kind in ("listed", "conditional", "range", "unknown"):
            rows = [q for q in quotes if q.platform == platform and q.price_kind == kind and q.match == "title_match"]
            if not rows:
                continue
            values = [q.price_low for q in rows if q.price_low is not None]
            groups.append({"platform": platform, "price_kind": kind, "count": len(rows),
                           "min": min(values) if values else None, "max": max(values) if values else None,
                           "median": float(median(values)) if values and kind in ("listed", "conditional") else None})
    return {"groups": groups, "total": len(quotes),
            "title_matched": sum(q.match == "title_match" for q in quotes),
            "unconfirmed_or_mismatched": sum(q.match != "title_match" for q in quotes),
            "basis": "本次采集的标题相关标价分布；附条件价格单列。规格、运费及优惠资格未核实，不代表同款成交价或全网最低价。"}
