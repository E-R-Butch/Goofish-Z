"""Drive the observed native sort menu and verify its request and rendered IDs."""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .errors import AuthRequiredError, GoofishError, RiskControlError

SEARCH_API = "mtop.taobao.idlemtopsearch.pc.search"
SORT_OPTIONS = {
    "default": ("", "", "综合"),
    "price_asc": ("price", "asc", "价格从低到高"),
    "price_desc": ("price", "desc", "价格从高到低"),
    "newest": ("create", "desc", "最新"),
}


def validate_sort(value: str) -> str:
    if value not in SORT_OPTIONS:
        raise ValueError("sort 必须为 default、price_asc、price_desc 或 newest")
    return value


def request_metadata(request: Any) -> dict:
    """Only keep provenance, never credentials, location or unrelated payload."""
    try:
        form = parse_qs(request.post_data or urlsplit(request.url).query)
        data = json.loads(form.get("data", ["{}"])[0])
        return {key: data.get(key) for key in ("keyword", "pageNumber", "sortField", "sortValue")}
    except (ValueError, TypeError, AttributeError):
        return {}


def is_search_response(response: Any, query: str) -> bool:
    return (f"/{SEARCH_API}/" in urlsplit(response.url).path
            and request_metadata(response.request).get("keyword") == query)


async def response_item_ids(response: Any, query: str, sort: str, number: int) -> list[str]:
    field, value, _ = SORT_OPTIONS[sort]
    meta = request_metadata(response.request)
    if meta != {"keyword": query, "pageNumber": number, "sortField": field, "sortValue": value}:
        raise GoofishError("闲鱼原生搜索的关键词、排序或页码不一致，已丢弃结果")
    if response.status >= 400:
        raise GoofishError(f"闲鱼原生排序请求失败（HTTP {response.status}）")
    try:
        raw = await response.json()
    except (ValueError, TypeError) as exc:
        raise GoofishError("闲鱼原生排序响应不是有效 JSON") from exc
    ret = raw.get("ret") if isinstance(raw, dict) else None
    if not isinstance(ret, list) or not ret or not all(str(code).startswith("SUCCESS::") for code in ret):
        from .mtop import _classify_error
        try:
            _classify_error({"ret": ret or ["EMPTY_RESPONSE"]}, SEARCH_API)
        except RiskControlError as exc:
            from .guard import trip
            trip(str(exc), api="search.browser.sort")
            raise
        raise GoofishError("闲鱼未确认原生排序请求成功")
    rows = (raw.get("data") or {}).get("resultList")
    if not isinstance(rows, list):
        raise GoofishError("原生排序结果缺少可核对的商品列表")
    try:
        ids = [str(row["data"]["item"]["main"]["exContent"]["itemId"]) for row in rows]
    except (KeyError, TypeError) as exc:
        raise GoofishError("原生排序商品结构已变化，无法确认页面结果") from exc
    if any(not item_id.isdecimal() for item_id in ids):
        raise GoofishError("原生排序商品标识无效")
    return ids


async def observe_sorted_action(page: Any, action: Any, query: str, sort: str, number: int) -> list[str]:
    from playwright.async_api import TimeoutError as BrowserTimeout
    try:
        async with page.expect_response(lambda r: is_search_response(r, query), timeout=20000) as received:
            await action()
        ids = await response_item_ids(await received.value, query, sort, number)
        await page.wait_for_function(
            """({ids, label, number}) => {
              const titles = [...document.querySelectorAll('span[class*="search-select-title--"]')];
              if (!titles.some(el => el.textContent.trim() === label)) return false;
              const active = document.querySelector('[class*="search-pagination-page-box-active"]');
              if ((Number(active?.textContent) || 1) !== number) return false;
              const cards = [...document.querySelectorAll('a[href*="/item?id="]')];
              if (!ids.length) return !cards.length && /暂无相关宝贝|未找到相关宝贝|没有找到/.test(document.body?.innerText || '');
              return ids.slice(0, 3).every((id, i) => cards[i] && new URL(cards[i].href).searchParams.get('id') === id);
            }""",
            arg={"ids": ids, "label": SORT_OPTIONS[sort][2], "number": number}, timeout=15000,
        )
        return ids
    except BrowserTimeout as exc:
        login = page.locator('[class*="login-modal-wrap"]')
        if await login.count() and await login.first.is_visible():
            raise AuthRequiredError("闲鱼原生排序需要登录，请更新登录态") from exc
        raise GoofishError("闲鱼原生排序或翻页未完成，未使用旧结果；请稍后重试") from exc


async def select_native_sort(page: Any, query: str, sort: str, wait_slot: Any) -> list[str]:
    from playwright.async_api import TimeoutError as BrowserTimeout
    from .guard import check as guard_check
    guard_check()
    try:
        title = "新发布" if sort == "newest" else "价格"
        await page.locator('span[class*="search-select-title--"]').filter(has_text=re.compile(f"^{re.escape(title)}$")).click(timeout=5000)
        await wait_slot()
        return await observe_sorted_action(
            page, lambda: page.get_by_text(SORT_OPTIONS[sort][2], exact=True).click(timeout=5000), query, sort, 1,
        )
    except BrowserTimeout as exc:
        raise GoofishError("闲鱼排序控件不可用，未退回默认排序") from exc


def verify_sorted_items(payload: dict, expected_ids: list[str], sort: str) -> None:
    ids = [parse_qs(urlsplit(item.get("url", "")).query).get("id", [""])[0] for item in payload.get("items", [])]
    if payload.get("source_sort") != sort or ids != expected_ids[:len(ids)] or (expected_ids and not ids):
        raise GoofishError("页面商品与原生排序响应不一致，已丢弃旧结果")
