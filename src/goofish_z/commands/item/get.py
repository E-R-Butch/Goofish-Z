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
    "minPrice",
    "maxPrice",
    "skuList",
)


def _extract_skus(item: dict[str, Any]) -> list[dict[str, Any]]:
    """提取商品多 SKU 真实规格矩阵 (规格名、真实到手价、库存)。"""
    raw_list = item.get("skuList") or item.get("idleItemSkuList") or []
    results = []
    for s in raw_list:
        price_val = s.get("price") or s.get("priceInCent") or 0
        price_yuan = float(price_val) / 100.0 if float(price_val) > 1000 else float(price_val)
        props = []
        for prop in s.get("propertyList", []):
            txt = prop.get("actualValueText") or prop.get("valueText") or ""
            if txt:
                props.append(txt)
        results.append({
            "sku_id": str(s.get("skuId") or ""),
            "name": " / ".join(props) if props else "默认规格",
            "price": price_yuan,
            "quantity": int(s.get("quantity") or 0),
        })
    return results


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
    min_p = item.get("minPrice")
    max_p = item.get("maxPrice")
    p_range = f"¥{min_p} ~ ¥{max_p}" if min_p and max_p and str(min_p) != str(max_p) else ""

    return {
        "item_id": str(item.get("itemId") or track.get("id") or item_id),
        "title": item.get("title") or track.get("title", ""),
        "price": f"¥{price}" if price and not str(price).startswith("¥") else price,
        "price_range": p_range,
        "seller_nick": seller.get("nick") or seller.get("uniqueName") or track.get("seller_nick", ""),
        "status": item.get("itemStatusStr") or track.get("itemStatus", ""),
        "skus": _extract_skus(item),
        "detail": _consumer_detail(item),
        "raw": raw,
    }
