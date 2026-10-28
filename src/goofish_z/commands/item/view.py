"""item view — 浏览器视角的商品详情。

两条链路：

1. **主链路** `core.item_page.read_item_page`：观察详情页自己发出的
   `mtop.taobao.idle.pc.detail` 响应，核对请求/响应身份与响应契约。字段从
   `data.itemDO / sellerDO / itemLabelExtList` 抽，比 `item get`（直签、浅抽）全。
2. **注入兜底** `_fetch_via_page_mtop`：只在「一次匹配请求都没看到」（SPA 未水合）时
   触发一次，与旧实现一致——在页面上下文里调 `window.lib.mtop.request`。它不用于
   重发请求：请求看到过但读取被打断时只等后续自然响应。

保留 `item get`（快且不需要 Chrome），两条路并存。
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from loguru import logger

from goofish_z.core import Strategy, command
from goofish_z.core.browser import goofish_page
from goofish_z.core.errors import AuthRequiredError, GoofishError
from goofish_z.core.item_page import (
    NoDetailRequestError,
    _decode_response,
    read_item_page,
)


def _normalize_item_id(value: Any) -> str:
    s = str(value or "").strip()
    if not re.fullmatch(r"\d+", s):
        raise GoofishError(f"item_id 必须是数字，收到：{value!r}")
    return s


def _build_item_url(item_id: str) -> str:
    return f"https://www.goofish.com/item?id={item_id}"


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def _text(value: Any) -> str:
    """计数类字段：None/空串保持空串，整数化的浮点不带 .0 尾巴。"""
    if value is None or value == "" or isinstance(value, bool):
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


def _label(labels: list[Any], name: str) -> str:
    for entry in labels:
        if isinstance(entry, dict) and _clean(entry.get("propertyText")) == name:
            return _clean(entry.get("text"))
    return ""


def _view_result(raw: dict[str, Any], item_id: str) -> dict[str, Any]:
    """把 mtop 详情响应映射成 item view 的扁平字段（与旧 JS 提取器逐字段对齐）。"""
    data = raw.get("data") or {}
    item = data.get("itemDO") or {}
    seller = data.get("sellerDO") or {}
    labels = item.get("itemLabelExtList") or []
    images = [
        _clean(entry.get("url"))
        for entry in item.get("imageInfos") or []
        if isinstance(entry, dict) and entry.get("url")
    ]
    title = _clean(item.get("title"))
    if not title:
        raise GoofishError(f"未拿到商品 {item_id} 的标题，接口返回为空", raw=raw)
    price = _clean(item.get("soldPrice") or item.get("defaultPrice"))
    seller_id = _text(seller.get("sellerId"))
    return {
        "item_id": _clean(item.get("itemId") or item_id),
        "title": title,
        "description": _clean(item.get("desc")),
        "price": f"¥{price}" if price else "",
        "original_price": _clean(item.get("originalPrice")),
        "want_count": _text(item.get("wantCnt")),
        "collect_count": _text(item.get("collectCnt")),
        "browse_count": _text(item.get("browseCnt")),
        "status": _clean(item.get("itemStatusStr")),
        "condition": _label(labels, "成色"),
        "brand": _label(labels, "品牌"),
        "category": _label(labels, "分类"),
        "location": _clean(seller.get("publishCity") or seller.get("city")),
        "seller_name": _clean(seller.get("nick") or seller.get("uniqueName")),
        "seller_id": seller_id,
        "seller_score": _clean(seller.get("xianyuSummary")),
        "reply_ratio_24h": _clean(seller.get("replyRatio24h")),
        "reply_interval": _clean(seller.get("replyInterval")),
        "seller_url": f"https://www.goofish.com/personal?userId={seller_id}" if seller_id else "",
        "image_count": str(len(images)),
        "image_urls": images,
    }


# 兜底路径在页面上下文里运行的抽取脚本（页面自己没发 detail 请求时才用）。
_FETCH_JS = r"""
(itemId) => (async () => {
  const clean = (v) => String(v ?? '').replace(/\s+/g, ' ').trim();
  const extractRetCode = (ret) => {
    const first = Array.isArray(ret) ? ret[0] : '';
    return clean(first).split('::')[0] || '';
  };
  const waitFor = async (predicate, timeoutMs = 5000) => {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (predicate()) return true;
      await new Promise((r) => setTimeout(r, 150));
    }
    return false;
  };

  const bodyText = document.body?.innerText || '';
  // 注意：「登录后」在页脚也会出现，不能作为 auth 判定；真正的未登录靠 mtop 返回的
  // FAIL_SYS_SESSION_EXPIRED 来判，这里只拦显式验证码/风控页。
  if (/验证码|安全验证|异常访问/.test(bodyText)) return { error: 'blocked' };

  await waitFor(() => window.lib?.mtop?.request);
  if (!window.lib || !window.lib.mtop || typeof window.lib.mtop.request !== 'function') {
    return { error: 'mtop-not-ready' };
  }

  let response;
  try {
    response = await window.lib.mtop.request({
      api: 'mtop.taobao.idle.pc.detail',
      data: { itemId: String(itemId) },
      type: 'POST',
      v: '1.0',
      dataType: 'json',
      needLogin: false,
      needLoginPC: false,
      sessionOption: 'AutoLoginOnly',
      ecode: 0,
    });
  } catch (error) {
    const ret = error?.ret || [];
    return {
      error: 'mtop-request-failed',
      error_code: extractRetCode(ret),
      error_message: clean(Array.isArray(ret) ? ret.join(' | ') : error?.message || String(error)),
    };
  }

  const retCode = extractRetCode(response?.ret || []);
  if (retCode && retCode !== 'SUCCESS') {
    return {
      error: 'mtop-response-error',
      error_code: retCode,
      error_message: clean((response?.ret || []).join(' | ')),
    };
  }
  // 原样带回整条 mtop 响应，交给 Python 的 _decode_response 做同一套契约校验。
  return { ok: true, raw: response };
})()
"""


async def _fetch_via_page_mtop(page: Any, item_id: str) -> dict[str, Any]:
    """兜底：页面没发过 detail 请求时，按旧实现注入一次 mtop 调用。

    成功时返回**校验过的响应体**（与主链路共用 `_decode_response` 的契约：ret 全
    SUCCESS、itemDO 结构、itemId 与请求一致），不再二次导航去等页面自己发请求。
    """
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception as exc:  # noqa: BLE001 - 超时后仍尝试注入，由 mtop 就绪判定兜住
        logger.debug("详情页 domcontentloaded 等待未完成（{}），继续尝试注入", type(exc).__name__)
    result = await page.evaluate(_FETCH_JS, item_id)

    if not isinstance(result, dict):
        raise GoofishError("商品详情页返回结构非预期")

    err = result.get("error")
    if err == "blocked":
        from goofish_z.core.errors import RiskControlError
        from goofish_z.core.guard import trip

        msg = "商品详情页被验证码/安全验证拦截，触发风控，已熔断"
        trip(msg, api="item.view.browser")
        raise RiskControlError(msg)
    if err == "mtop-not-ready":
        raise GoofishError("页面 window.lib.mtop 未就绪（等待超时），页面加载异常")

    # auth 判定统一走 mtop 返回的 error_code/error_message 里的 SESSION_EXPIRED，
    # 不再依赖 body 文案正则（\"登录后\" 在页脚也会出现，误报多）。
    err_code = str(result.get("error_code") or "")
    err_msg = str(result.get("error_message") or "")
    if re.search(r"FAIL_SYS_SESSION_EXPIRED|SESSION_EXPIRED", err_code + " " + err_msg):
        raise AuthRequiredError("mtop 返回 session 过期：" + (err_msg or err_code))
    if err:
        raise GoofishError(f"mtop 调用失败 [{err_code or err}]: {err_msg or '无详情'}")
    raw = result.get("raw")
    if result.get("ok") is not True or not isinstance(raw, dict):
        raise GoofishError("兜底注入未返回可确认的商品详情响应")
    return _decode_response(json.dumps(raw, ensure_ascii=False), item_id)


async def _run(item_id: str) -> dict[str, Any]:
    async with goofish_page() as page:
        try:
            raw = await read_item_page(page, item_id)
        except NoDetailRequestError:
            # 页面已加载但一次匹配请求都没发出：这才允许注入兜底，
            # 直接用注入拿到的响应，不再二次导航去等页面自己发请求。
            logger.debug("详情页未发出 detail 请求，回退注入式读取 item_id={}", item_id)
            raw = await _fetch_via_page_mtop(page, item_id)
    return _view_result(raw, item_id)


@command(
    namespace="item",
    name="view",
    description="浏览器视角查看商品详情（字段比 item get 更全，抗风控）",
    strategy=Strategy.COOKIE,
    columns=[
        "item_id", "title", "price", "condition", "brand", "location",
        "seller_name", "want_count", "browse_count",
    ],
)
def view(item_id: str) -> dict[str, Any]:
    from goofish_z.core.limiter import check as rate_check

    normalized = _normalize_item_id(item_id)
    rate_check("detail")
    # MCP / table 渲染依赖 dict；image_urls 是列表（JSON 里保留，table 里压成长度）
    result = asyncio.run(_run(normalized))
    result["_image_urls_json"] = json.dumps(result.get("image_urls", []), ensure_ascii=False)
    return result


__test__ = {
    "_normalize_item_id": _normalize_item_id,
    "_build_item_url": _build_item_url,
    "_view_result": _view_result,
}
