"""search — 搜索闲鱼商品。对标 OpenCLI `xianyu/search.js`。

思路：打开 `https://www.goofish.com/search?q=xxx` 让页面自己渲染，autoScroll 触发
懒加载，再在 page context 里跑 DOM 选择器提卡片。**不走 mtop 直签**：
- search 没对外 API，只有 HTML 卡片 + 动态加载
- 浏览器真实渲染天然抗风控

字段参考 OpenCLI：`item_id / rank / title / price / original_price / condition /
brand / location / badge / url / extra`。
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from goofish_z.core import Strategy, command
from goofish_z.core.browser import auto_scroll, goofish_page
from goofish_z.core.errors import AuthRequiredError, GoofishError
from goofish_z.core.price import normalize_price

MAX_LIMIT = 50
MAX_PAGE = 50


def _normalize_limit(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 20
    return min(MAX_LIMIT, max(1, n))


def _item_id_from_url(url: str) -> str:
    m = re.search(r"[?&]id=(\d+)", url or "")
    return m.group(1) if m else ""


def _build_search_url(query: str) -> str:
    from urllib.parse import quote
    return f"https://www.goofish.com/search?q={quote(query)}"


# 页面上下文里跑的 JS。抽出来做常量方便测试（`__test__` 导出）。
_EXTRACT_JS = r"""
(limit) => (async () => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const waitFor = async (predicate, timeoutMs = 8000) => {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (predicate()) return true;
      await wait(150);
    }
    return false;
  };

  const clean = (v) => (v || '').replace(/\s+/g, ' ').trim();
  const sel = {
    card: 'a[href*="/item?id="]',
    title: '[class*="row1-wrap-title"], [class*="main-title"]',
    attrs: '[class*="row2-wrap-cpv"] span[class*="cpv--"]',
    priceWrap: '[class*="price-wrap"]',
    priceNum: '[class*="number"]',
    priceDec: '[class*="decimal"]',
    priceUnit: '[class*="magnitude"]',
    priceDesc: '[class*="price-desc"] [title], [class*="price-desc"] [style*="line-through"]',
    sellerWrap: '[class*="row4-wrap-seller"]',
    sellerText: '[class*="seller-text"]',
    badge: '[class*="credit-container"] [title], [class*="credit-container"] span',
  };

  await waitFor(() => {
    const bodyText = document.body?.innerText || '';
    return Boolean(
      document.querySelector(sel.card)
      || /请先登录|登录后|验证码|安全验证|异常访问/.test(bodyText)
      || /暂无相关宝贝|未找到相关宝贝|没有找到/.test(bodyText)
    );
  });

  const bodyText = document.body?.innerText || '';
  const requiresAuth = /请先登录|登录后/.test(bodyText);
  const blocked = /验证码|安全验证|异常访问/.test(bodyText);
  const empty = /暂无相关宝贝|未找到相关宝贝|没有找到/.test(bodyText);

  const items = Array.from(document.querySelectorAll(sel.card))
    .slice(0, limit)
    .map((card) => {
      const href = card.href || card.getAttribute('href') || '';
      const title = clean(card.querySelector(sel.title)?.textContent || '');
      const attrs = Array.from(card.querySelectorAll(sel.attrs))
        .map((n) => clean(n.textContent || ''))
        .filter(Boolean);
      const priceWrap = card.querySelector(sel.priceWrap);
      const priceNumber = clean(priceWrap?.querySelector(sel.priceNum)?.textContent || '');
      const priceDecimal = clean(priceWrap?.querySelector(sel.priceDec)?.textContent || '');
      const priceUnit = clean(card.querySelector(sel.priceUnit)?.textContent || '');
      const location = clean(card.querySelector(sel.sellerWrap)?.querySelector(sel.sellerText)?.textContent || '');
      const originalPriceNode = card.querySelector(sel.priceDesc);
      const badgeNode = card.querySelector(sel.badge);

      return {
        title,
        url: href,
        price: priceNumber ? clean('¥' + priceNumber + priceDecimal + priceUnit) : '',
        original_price: clean(originalPriceNode?.getAttribute('title') || originalPriceNode?.textContent || ''),
        condition: attrs[0] || '',
        brand: attrs[1] || '',
        extra: attrs.slice(2).join(' | '),
        location,
        badge: clean(badgeNode?.getAttribute('title') || badgeNode?.textContent || ''),
      };
    })
    .filter((it) => it.title && it.url);

  const activePage = document.querySelector('[class*="search-pagination-page-box-active"]');
  const next = document.querySelector('[class*="search-pagination-arrow-right"]')?.closest('button');
  return {
    requiresAuth, blocked, empty, items, bodyPreview: bodyText.slice(0, 500),
    page: Number(activePage?.textContent) || 1,
    has_next: Boolean(next && !next.disabled),
    source_count: document.querySelectorAll(sel.card).length,
  };
})()
"""


async def _wait_for_page_slot() -> None:
    """A page change is another search, so it reserves the shared search bucket."""
    from goofish_z.core.errors import RateLimitedError
    from goofish_z.core.guard import check as guard_check
    from goofish_z.core.limiter import check as rate_check

    guard_check()
    try:
        rate_check("search")
    except RateLimitedError as error:
        await asyncio.sleep((error.retry_after or 1) + 0.1)
        guard_check()
        rate_check("search")


async def _go_to_page(page: Any, number: int) -> None:
    """Use the site's observed page picker; never bypass a login overlay."""
    from playwright.async_api import TimeoutError as BrowserTimeout

    login = page.locator('[class*="login-modal-wrap"]')
    if await login.count() and await login.first.is_visible():
        raise AuthRequiredError("闲鱼翻页需要登录，请在 Chrome 登录后重新导入登录态")
    picker = page.locator('[class*="search-pagination-to-page-input--"]')
    if not await picker.count():
        raise GoofishError("搜索页没有可用的翻页控件")
    previous = await page.locator('a[href*="/item?id="]').evaluate_all(
        "cards => cards.map(card => card.getAttribute('href')).join('|')"
    )
    await picker.fill(str(number))
    await _wait_for_page_slot()
    try:
        await page.locator('[class*="search-pagination-to-page-confirm-button"]').click(timeout=5000)
        await page.wait_for_function(
            """({number, previous}) => {
              const active = document.querySelector('[class*="search-pagination-page-box-active"]');
              const cards = [...document.querySelectorAll('a[href*="/item?id="]')];
              return Number(active?.textContent) === number && cards.length > 0 &&
                cards.map(card => card.getAttribute('href')).join('|') !== previous;
            }""",
            arg={"number": number, "previous": previous}, timeout=12000,
        )
    except BrowserTimeout as error:
        if await login.count() and await login.first.is_visible():
            raise AuthRequiredError("闲鱼翻页需要重新登录，请更新 Chrome 登录态") from error
        raise GoofishError("闲鱼翻页未完成，请稍后重试；未将旧页当成新结果") from error


async def _run(query: str, limit: int, page_number: int = 1) -> dict[str, Any]:
    url = _build_search_url(query)
    async with goofish_page() as page:
        await page.goto(url, wait_until="domcontentloaded")
        payload = await _read_search_page(page, limit)
        _check_payload(payload)
        if page_number > 1:
            if not payload.get("has_next"):
                raise GoofishError("没有更多搜索结果")
            await _go_to_page(page, page_number)
            payload = await _read_search_page(page, limit)
            _check_payload(payload)
            if payload.get("page") != page_number:
                raise GoofishError("闲鱼返回的页码不匹配，请重新搜索")

    items = payload.get("items") or []
    return {
        "items": [
            {**it, **normalize_price(it.get("price")),
             "rank": i + 1, "item_id": _item_id_from_url(it.get("url", ""))}
            for i, it in enumerate(items)
        ],
        "page": page_number,
        "has_next": bool(payload.get("has_next")) and page_number < MAX_PAGE,
        "source_count": payload.get("source_count", len(items)),
    }


async def _read_search_page(page: Any, limit: int) -> dict[str, Any]:
    """An authentication redirect can replace the DOM during the first read."""
    from playwright.async_api import Error as BrowserError

    for attempt in range(2):
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)
            await auto_scroll(page, times=2)
            return await page.evaluate(_EXTRACT_JS, limit)
        except BrowserError as error:
            if attempt or "Execution context was destroyed" not in str(error):
                raise
            # Read the new document once; do not re-submit a search or dismiss login.
    raise GoofishError("搜索页跳转未完成")


def _check_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise GoofishError("搜索页返回结构非预期")

    items = payload.get("items") or []
    # "登录后" 在页脚也会出现——只有在"没拿到卡片 && 命中关键词"时才判定 auth 失败
    if not items and payload.get("requiresAuth"):
        raise AuthRequiredError("www.goofish.com 搜索结果页要求登录，cookies 可能失效")
    if not items and payload.get("blocked"):
        # 浏览器级风控（滑块/验证码）→ 触发熔断（hard 级），停止后续自动化
        from goofish_z.core.errors import RiskControlError
        from goofish_z.core.guard import trip

        msg = "搜索页返回验证码/安全验证（触发风控），已熔断"
        trip(msg, api="search.browser")
        raise RiskControlError(msg)

    if not items and not payload.get("empty"):
        preview = (payload.get("bodyPreview") or "")[:200]
        raise GoofishError(
            f"未在搜索页上解析到任何卡片，可能 DOM 结构已变。"
            f"页面文案预览：{preview!r}"
        )


@command(
    namespace="search",
    name="items",
    description="按页搜索闲鱼商品（浏览器路径，limit 为当前页条数上限，page 为页码）",
    strategy=Strategy.COOKIE,
    columns=["rank", "item_id", "title", "price", "condition", "brand", "location", "badge", "url"],
)
def search(query: str, limit: int = 20, filter_blacklist: bool = True, page: int = 1) -> dict[str, Any]:
    # 限流：搜索间隔 30s（防接口级风控）
    from goofish_z.core.limiter import check as rate_check
    from goofish_z.core.guard import check as guard_check

    if not str(query).strip():
        raise ValueError("搜索关键词不能为空")
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= MAX_PAGE:
        raise ValueError(f"页码必须是 1 到 {MAX_PAGE} 的整数")
    guard_check()
    rate_check("search")
    fetched = asyncio.run(_run(str(query).strip(), _normalize_limit(limit), page))
    items = fetched["items"]
    fetched_count = len(items)

    # 噪音过滤（UNIVERSAL 硬规则）：收购帖过滤——买家是来买东西的，
    # 不是看收购广告的。任何搜索都必须过滤（用户明确要求）。
    from goofish_z.blacklist import is_noise

    clean = []
    for it in items:
        noise = is_noise(it)
        if noise:
            it["_noise"] = noise
        else:
            clean.append(it)
    items = clean

    # 容量校验：query 含容量（如 32G）时，过滤搜索结果里的异容量污染
    # （闲鱼模糊搜索会把 16G 混进 32G 的结果——2026-08-08 实测 20 条里 3 条污染）
    from goofish_z.blacklist import (
        _extract_capacity,
        capacity_matches,
        extract_generation,
        is_broken_stick,
    )

    req_cap = _extract_capacity(str(query))
    if req_cap:
        filtered = []
        for it in items:
            if capacity_matches(str(it.get("title", "")), req_cap):
                filtered.append(it)
            else:
                it["_cap_mismatch"] = f"搜索{req_cap}G但商品容量不匹配"
        items = filtered

    # 代数校验：query 含 DDRx 时，代数不匹配的过滤（DDR4 混进 DDR3 搜索）。
    # 例外：坏条/报废条且价格极低（练手/拆件价值）保留——DDR3 坏条 ¥10 有人买。
    req_gen = extract_generation(str(query))
    if req_gen:
        filtered = []
        for it in items:
            gen = extract_generation(str(it.get("title", "")))
            if gen is None or gen == req_gen:
                # 代数匹配（或无信息不误杀）。匹配的坏条打标供展示，不参与过滤
                if gen == req_gen and is_broken_stick(it):
                    it["_broken_stick"] = True
                filtered.append(it)
            else:
                # 代数不匹配。DDR3 搜索里混进的 DDR4 一律过滤——
                # 即使标了坏条/报废（买家要的是 DDR3，DDR4 坏条无练手价值）。
                it["_gen_mismatch"] = f"搜索{req_gen}但商品是{gen}"
        items = filtered
    result: dict[str, Any] = {
        **fetched, "items": items, "count": len(items),
        "filtered_count": fetched_count - len(items),
    }

    if filter_blacklist:
        from goofish_z.blacklist import BlacklistDB
        from pathlib import Path

        from goofish_z.core.paths import runtime_data_path

        db = BlacklistDB(runtime_data_path("watch.db"))
        passed, blocked = db.filter_items(items)
        result["items"] = passed
        result["count"] = len(passed)
        result["blocked"] = [
            {
                "title": b.get("title", "")[:60],
                "price": b.get("price"),
                "reasons": b.get("_blocked_reasons", []),
            }
            for b in blocked
        ]
        result["blocked_count"] = len(blocked)
    return result


__test__ = {
    "MAX_LIMIT": MAX_LIMIT,
    "_normalize_limit": _normalize_limit,
    "_build_search_url": _build_search_url,
    "_item_id_from_url": _item_id_from_url,
}
