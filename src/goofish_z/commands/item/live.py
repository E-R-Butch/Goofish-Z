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

_PROBE_JS = """() => {
  const t = (document.body && document.body.innerText) || '';
  return { title: document.title || '', len: t.length, head: t.replace(/\\s+/g, ' ').slice(0, 120) };
}"""


def judge_item_page(payload: dict[str, Any]) -> str:
    """商品页标题一旦变成宝贝标题就是活的；通用标题/空标题视为死链。"""
    title = str(payload.get("title") or "").strip()
    if not title or title == GENERIC_TITLE:
        return "offline"
    return "live"


# 兼容旧名（容器内的原实现用 _judge）
_judge = judge_item_page


async def _verify_batch(item_ids: list[str]) -> dict[str, str]:
    """串行核对（同一页面复用）；单条异常归 unknown，不影响其它条。"""
    states: dict[str, str] = {}
    async with warm_page() as page:
        for item_id in item_ids:
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
                    state = judge_item_page(payload if isinstance(payload, dict) else {})
                    if state == "live" or time.monotonic() >= deadline:
                        break
                states[item_id] = state
            except Exception:  # noqa: BLE001 - 单条失败不能带崩整批
                states[item_id] = "unknown"
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
    return asyncio.run(
        run_in_browser(_verify_batch, ids[:MAX_VERIFY], timeout=20.0 + 10.0 * len(ids))
    )


__test__ = {
    "_judge": judge_item_page,
    "judge_item_page": judge_item_page,
    "GENERIC_TITLE": GENERIC_TITLE,
}
