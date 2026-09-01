"""item mine — 列出当前账号发布过的商品，并可靠区分在售与已卖出。

个人主页的 DOM 卡片不展示商品状态，不能据此判断是否仍在售。这里直接调用主页
自身使用的 ``mtop.idle.web.xyh.item.list``，读取每张卡片的 ``itemStatus``：

- ``0``: online（在线）
- ``1``: sold（卖掉了）
- 其他值: offline（其他非在售状态，保留原始 ``status_code``）

默认只返回 online，避免 Agent 把历史已售商品误当成可售库存。
"""
from __future__ import annotations

import json
import time
from typing import Any

from goofish_z.core import Session, Strategy, command
from goofish_z.core.errors import GoofishError, RateLimitedError
from goofish_z.core.mtop import call

MAX_LIMIT = 200
PAGE_SIZE = 20
MAX_PAGES = 20
_API = "mtop.idle.web.xyh.item.list"


def _normalize_limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 20
    return min(MAX_LIMIT, max(1, limit))


def _normalize_status(value: Any) -> str:
    raw = str(value or "online").strip().lower().replace("-", "_")
    aliases = {
        "online": "online",
        "active": "online",
        "on_sale": "online",
        "在售": "online",
        "在线": "online",
        "sold": "sold",
        "卖掉了": "sold",
        "已卖出": "sold",
        "offline": "offline",
        "inactive": "offline",
        "下架": "offline",
        "已下架": "offline",
        "all": "all",
        "全部": "all",
    }
    normalized = aliases.get(raw)
    if not normalized:
        choices = "online/sold/offline/all"
        raise GoofishError(f"status 仅支持 {choices}，收到：{value!r}")
    return normalized


def _status_name(value: Any) -> str:
    try:
        code = int(value)
    except (TypeError, ValueError):
        return "offline"
    if code == 0:
        return "online"
    if code == 1:
        return "sold"
    return "offline"


def _parse_image_urls(detail: dict[str, Any], pic: dict[str, Any]) -> list[str]:
    raw = detail.get("imageInfos") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = []
    images = raw if isinstance(raw, list) else []
    urls = [str(image.get("url") or "") for image in images if isinstance(image, dict)]
    urls = [url for url in urls if url]
    fallback = str(pic.get("picUrl") or detail.get("picUrl") or "")
    if fallback and fallback not in urls:
        urls.insert(0, fallback)
    return urls


def _parse_card(card: dict[str, Any]) -> dict[str, Any] | None:
    data = card.get("cardData") or {}
    if not isinstance(data, dict):
        return None
    item_id = str(data.get("id") or "").strip()
    title = str(data.get("title") or "").strip()
    if not item_id or not title:
        return None

    detail = data.get("detailParams") or {}
    price_info = data.get("priceInfo") or {}
    pic = data.get("picInfo") or {}
    if not isinstance(detail, dict):
        detail = {}
    if not isinstance(price_info, dict):
        price_info = {}
    if not isinstance(pic, dict):
        pic = {}

    status_code = data.get("itemStatus")
    image_urls = _parse_image_urls(detail, pic)
    price = str(price_info.get("price") or detail.get("soldPrice") or "").strip()
    return {
        "item_id": item_id,
        "title": title,
        "price": f"¥{price}" if price and not price.startswith("¥") else price,
        "status": _status_name(status_code),
        "status_code": status_code,
        "category_id": str(data.get("categoryId") or ""),
        "post_info": str(detail.get("postInfo") or "").strip(),
        "url": f"https://www.goofish.com/item?id={item_id}",
        "image_url": image_urls[0] if image_urls else "",
        "image_urls": image_urls,
    }


def _matches_status(item: dict[str, Any], status_filter: str) -> bool:
    return status_filter == "all" or item.get("status") == status_filter


def _wait_for_seller_page_slot() -> None:
    """Wait until one seller-page request is allowed.

    ``mine(limit=200)`` can need several API pages.  Consuming one limiter token
    for the whole command would let those requests burst, so every page obtains
    its own slot.  Sleeping here keeps pagination usable while preserving the
    process-shared limiter's conservative cadence.
    """
    from goofish_z.core.limiter import check as rate_check
    from goofish_z.core.limiter import status as limiter_status

    while True:
        try:
            rate_check("seller_page")
            return
        except RateLimitedError:
            bucket = limiter_status().get("seller_page", {})
            try:
                wait_seconds = float(bucket.get("next_available_in") or 0)
            except (TypeError, ValueError):
                wait_seconds = 0
            time.sleep(max(0.2, wait_seconds + 0.1))


@command(
    namespace="item",
    name="mine",
    description="列出当前账号发布的商品；默认仅在售，可筛选已卖出/已下架/全部",
    strategy=Strategy.COOKIE,
    columns=["item_id", "title", "price", "status", "category_id", "url"],
)
def mine(status: str = "online", limit: int = 20) -> dict[str, Any]:
    """列出本人发布过的商品。

    ``status`` 支持 ``online``（默认）、``sold``、``offline``、``all``。
    ``limit`` 是过滤后的最大返回数，而不是主页扫描数。
    """
    status_filter = _normalize_status(status)
    result_limit = _normalize_limit(limit)

    session = Session.load()
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    scanned_count = 0
    page_number = 1
    has_more = True
    reported_total_count = 0

    while has_more and page_number <= MAX_PAGES and len(items) < result_limit:
        _wait_for_seller_page_slot()
        raw = call(
            session,
            api=_API,
            data={
                "needGroupInfo": page_number == 1,
                "pageNumber": page_number,
                "userId": session.unb,
                "pageSize": PAGE_SIZE,
            },
            version="1.0",
            spm_cnt="a21ybx.user.0.0",
        )
        data = raw.get("data") or {}
        cards = data.get("cardList") or []
        if not isinstance(cards, list):
            raise GoofishError("个人主页商品列表返回结构非预期：cardList 不是数组")

        try:
            reported_total_count = max(reported_total_count, int(data.get("totalCount") or 0))
        except (TypeError, ValueError):
            pass

        for card in cards:
            if not isinstance(card, dict):
                continue
            item = _parse_card(card)
            if not item or item["item_id"] in seen_ids:
                continue
            seen_ids.add(item["item_id"])
            scanned_count += 1
            if _matches_status(item, status_filter):
                items.append(item)
                if len(items) >= result_limit:
                    break

        has_more = bool(data.get("nextPage")) and bool(cards)
        page_number += 1

    payload: dict[str, Any] = {
        "items": items[:result_limit],
        "count": min(len(items), result_limit),
        "status_filter": status_filter,
        "scanned_count": scanned_count,
        "pages_fetched": page_number - 1,
        "has_more": has_more,
    }
    if reported_total_count:
        payload["reported_total_count"] = reported_total_count
    return payload


__test__ = {
    "MAX_LIMIT": MAX_LIMIT,
    "_normalize_limit": _normalize_limit,
    "_normalize_status": _normalize_status,
    "_status_name": _status_name,
    "_parse_card": _parse_card,
    "_matches_status": _matches_status,
    "_wait_for_seller_page_slot": _wait_for_seller_page_slot,
}
