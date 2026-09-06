"""Convert a listing's displayed price to yuan without dropping its unit."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any


def price_value(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    text = re.sub(r"\s+", "", str(value)).replace(",", "").replace("，", "")
    match = re.fullmatch(r"[¥￥]?(\d+(?:\.\d+)?)(万|千)?元?", text)
    if not match:
        return None
    try:
        amount = Decimal(match[1]) * {None: 1, "万": 10000, "千": 1000}[match[2]]
        return float(amount.quantize(Decimal("0.01")))
    except InvalidOperation:
        return None


def normalize_price(value: Any) -> dict[str, Any]:
    text = str(value if value is not None else "").strip()
    amount = price_value(value)
    # Keep the source text too: compact prices may be rounded by the marketplace.
    return {
        "price": f"¥{amount:.2f}".rstrip("0").rstrip(".") if amount is not None else text,
        "price_value": amount,
        "price_text": text,
    }
