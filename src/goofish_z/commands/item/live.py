"""商品在售状态校验（渲染商品页判定，不调 detail 接口）。

闲鱼搜索列表会保留已下架/已删除的卡片，卡片本身没有任何「已售」或「已下架」标记：
点进去只有页脚空壳。用户拿到的链接必须是能打开的商品，所以在把结果交给用户之前
逐条渲染核对。

判据（2026-10-28 实测）：只看 `document.title`。

- 已下架/已删除：标题恒为站点通用标题 `闲鱼 - 闲不住？上闲鱼！`，body 约 600 字。
- 在售：标题是「宝贝标题_闲鱼」，body 三千字以上。

早期版本用 body 长度判定，会被 SPA 水合快慢误杀（同一商品隔十分钟判出两种结果），
所以长度只留作排障留痕，不参与判决。

**不要并发开页**：在常驻 profile 上并发 `new_page()` 实测会把在售商品全判成空壳
（并发 2 条 → 4 条全 offline；串行 → 3 live / 1 offline）。这里串行复用同一个 page。
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Iterable
from typing import Any

from goofish_z.core.browser import run_in_browser, warm_page

GENERIC_TITLE = "闲鱼 - 闲不住？上闲鱼！"
MAX_VERIFY = int(os.environ.get("GOOFISH_LIVE_MAX_ITEMS", "8"))
PAGE_TIMEOUT_MS = int(os.environ.get("GOOFISH_LIVE_PAGE_TIMEOUT_MS", "9000"))
SETTLE_MS = int(os.environ.get("GOOFISH_LIVE_SETTLE_MS", "300"))
# 已下架页永远停在通用标题上，只是 SPA 水合有快慢：给一个预算反复探测，
# 预算内没变成商品标题才判 offline（避免把慢加载的好商品误杀）。
BUDGET_S = float(os.environ.get("GOOFISH_LIVE_BUDGET_SECONDS", "3.5"))

# 商品页标题是「宝贝标题_闲鱼」；登录页/验证页/目录页的标题不是这个形状。
PRODUCT_TITLE_SUFFIX = "_闲鱼"
# 命中这些文案说明落在登录或风控页，绝对不能当「在售」。
VERIFICATION_MARKERS = ("验证码", "安全验证", "异常访问", "请登录")

_PROBE_JS = """() => {
  const t = (document.body && document.body.innerText) || '';
  return { title: document.title || '', len: t.length, head: t.replace(/\\s+/g, ' ').slice(0, 120) };
}"""


def looks_like_verification(payload: dict[str, Any]) -> bool:
    """落在登录/验证页？标题和正文开头任一带标记即算。"""
    text = f"{payload.get('title') or ''} {payload.get('head') or ''}"
    return any(marker in text for marker in VERIFICATION_MARKERS)


def judge_item_page(payload: dict[str, Any]) -> str:
    """判活必须看到**商品页证据**，而不是「标题非通用」。

    访问商品页可能被导到登录页或安全验证页，那些页面的标题既不为空也不是站点通用
    标题——只按「非通用标题即 live」会把它们判成在售，搜索还会给用户打上
    `live_verified`。所以：验证页 → unknown；通用/空标题 → 死链；标题必须是
    「宝贝标题_闲鱼」形状才算 live，其余（目录页、跳转页）一律 unknown。
    """
    title = str(payload.get("title") or "").strip()
    if looks_like_verification(payload):
        return "unknown"
    if not title or title == GENERIC_TITLE:
        return "offline"
    if not title.endswith(PRODUCT_TITLE_SUFFIX):
        return "unknown"
    return "live"


# 兼容旧名（容器内的原实现用 _judge）
_judge = judge_item_page


async def _verify_batch(item_ids: list[str]) -> dict[str, str]:
    """串行核对；普通单条异常归 unknown，登录/验证页立即停止整批。"""
    states: dict[str, str] = dict.fromkeys(item_ids, "unknown")
    async with warm_page() as page:
        for item_id in item_ids:
            stop_batch = False
            try:
                await page.goto(
                    f"https://www.goofish.com/item?id={item_id}",
                    wait_until="domcontentloaded",
                    timeout=PAGE_TIMEOUT_MS,
                )
                deadline = time.monotonic() + BUDGET_S
                state = "offline"
                while True:
                    await page.wait_for_timeout(SETTLE_MS)
                    payload = await page.evaluate(_PROBE_JS)
                    payload = payload if isinstance(payload, dict) else {}
                    if looks_like_verification(payload):
                        # 命中验证立即停批：保留已完成结果，当前与未访问项仍为 unknown。
                        # 先置停止标记，避免熔断状态写入失败后被单条异常处理放行。
                        stop_batch = True
                        state = "unknown"
                        from goofish_z.core.guard import trip

                        trip("在售校验落在登录/验证页", api="item.live")
                        break
                    state = judge_item_page(payload)
                    if state == "live" or time.monotonic() >= deadline:
                        break
                states[item_id] = state
            except Exception:  # noqa: BLE001 - 单条失败不能带崩整批
                states[item_id] = "unknown"
            if stop_batch:
                break
    return states


def check_items_live(item_ids: Iterable[str]) -> dict[str, str]:
    """返回 {item_id: live|offline|unknown}；去重、只认数字、最多校验 MAX_VERIFY 条。"""
    ids: list[str] = []
    for raw in item_ids:
        text = str(raw or "").strip()
        if text.isdigit() and text not in ids:
            ids.append(text)
    if not ids:
        return {}
    selected = ids[:MAX_VERIFY]
    # 批量超时必须由每条的页面超时与探测预算推导：写死常数在调大
    # GOOFISH_LIVE_BUDGET_SECONDS / PAGE_TIMEOUT_MS 之后会先把整批掐断。
    # 期限在轮询等待之后才检查，最后一轮还可能多等一个完整 SETTLE_MS。
    per_item = PAGE_TIMEOUT_MS / 1000.0 + BUDGET_S + SETTLE_MS / 1000.0 + 2.0
    budget = 10.0 + per_item * len(selected)
    return asyncio.run(run_in_browser(_verify_batch, selected, timeout=budget))


__test__ = {
    "_judge": judge_item_page,
    "judge_item_page": judge_item_page,
    "GENERIC_TITLE": GENERIC_TITLE,
}
