"""item get — 查询商品详情。接口 mtop.taobao.idle.pc.detail v1.0（只读）"""

from typing import Any

from goofish_z.core import Session, Strategy, command
from goofish_z.core.mtop import call


_DETAIL_FIELDS = (
    "title",
    "desc",
    "itemStatusStr",
    "itemLabelExtList",
    "imageInfos",
)


def _consumer_detail(item: dict[str, Any]) -> dict[str, Any]:
    """Return only fields needed by local read-only consumers."""
    return {field: item[field] for field in _DETAIL_FIELDS if field in item}


@command(
    namespace="item",
    name="get",
    description="查询闲鱼商品详情（只读）",
    strategy=Strategy.COOKIE,
    columns=["item_id", "title", "price", "seller_nick", "status"],
)
def get(item_id: str) -> dict[str, Any]:
    from goofish_z.core.limiter import check as rate_check

    rate_check("detail")
    session = Session.load()
    raw = call(
        session,
        api="mtop.taobao.idle.pc.detail",
        data={"itemId": str(item_id)},
        version="1.0",
        spm_cnt="a21ybx.item.0.0",
    )
    data = raw.get("data", {}) or {}
    item = data.get("itemDO", {}) or {}
    seller = data.get("sellerDO", {}) or {}
    track = data.get("trackParams", {}) or {}
    price = item.get("soldPrice") or item.get("defaultPrice") or track.get("soldPrice") or track.get("price", "")
    return {
        "item_id": str(item.get("itemId") or track.get("id") or item_id),
        "title": item.get("title") or track.get("title", ""),
        "price": f"¥{price}" if price and not str(price).startswith("¥") else price,
        "seller_nick": seller.get("nick") or seller.get("uniqueName") or track.get("seller_nick", ""),
        "status": item.get("itemStatusStr") or track.get("itemStatus", ""),
        "detail": _consumer_detail(item),
        "raw": raw,
    }
