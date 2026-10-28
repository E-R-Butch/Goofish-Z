"""读取商品详情页自身发出的 mtop 响应，替代注入式 mtop 调用。

为什么这么改（上游 goofish-cli v0.6.0–v0.6.3 的做法）：`item view` 过去是
「goto(domcontentloaded) + 固定等 2s + 在页面上下文里调 window.lib.mtop.request」。
三个真问题——

1. 把时序当契约：页面水合慢于固定等待就报 mtop-not-ready，而慢是常态不是错误。
2. 注入调用是页面本不该有的额外请求，拿到响应也不核对身份，串号（拿到别的商品）
   无法发现；请求被打断时也没有「只等后续自然响应、不补发」的语义。
3. 响应契约不校验：ret 被风控替换、data.itemDO 结构变了都会静默通过。

现在改为观察详情页自己发起的 `mtop.taobao.idle.pc.detail`：
URL（host+path）与请求体 itemId 双向核对 → ret 必须全 SUCCESS → data/itemDO、
sellerDO、itemLabelExtList、imageInfos 结构合规 → 响应 itemId 与请求一致。
导航打断响应体读取时只等后续自然响应，不主动补发；页面完全没发请求（未水合）
时抛 `NoDetailRequestError`，由调用方决定是否走原来的注入兜底。
"""
from __future__ import annotations

import asyncio
import json
import math
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

try:  # 离线环境（无 playwright）也允许导入本模块做纯逻辑测试
    from playwright.async_api import Error as PlaywrightError
except ImportError:  # pragma: no cover - 生产环境 playwright 是硬依赖

    class PlaywrightError(Exception):  # type: ignore[no-redef]
        """playwright 缺失时的占位，使 except 子句仍然成立。"""


from .errors import GoofishError, RiskControlError, UpstreamError
from .mtop import _classify_error

DETAIL_API = "mtop.taobao.idle.pc.detail"
DETAIL_PATH = "/h5/mtop.taobao.idle.pc.detail/1.0/"
DETAIL_HOSTS = frozenset({"h5api.m.goofish.com", "h5api.m.taobao.com"})
DETAIL_TIMEOUT = 20.0
ITEM_URL = "https://www.goofish.com/item?id={item_id}"

# SDK 自行刷新令牌时的中间态，不代表用户登录态失效（上游实现同时出现两种拼写）。
_TOKEN_REFRESH_CODES = frozenset(
    {"FAIL_SYS_TOKEN_EMPTY", "FAIL_SYS_TOKEN_EXPIRED", "FAIL_SYS_TOKEN_EXOIRED"}
)


class NoDetailRequestError(GoofishError):
    """页面已加载但从未发出目标商品的详情请求（通常是 SPA 未水合）。"""


def _matches_request(request: Any, item_id: str) -> bool:
    """只认详情接口本身，且请求体里的 itemId 必须就是目标商品。"""
    url = urlsplit(getattr(request, "url", "") or "")
    if (url.hostname or "") not in DETAIL_HOSTS or url.path != DETAIL_PATH:
        return False
    try:
        fields = parse_qs(request.post_data or "")
        if "data" not in fields:
            fields = parse_qs(url.query)
        return str(json.loads(fields["data"][0]).get("itemId")) == item_id
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        return False


def _decode_response(text: str, item_id: str) -> dict:
    """响应契约的唯一闸门：解析、业务状态、结构、身份，任一不合规都抛错。"""
    body = text.lstrip()
    if not body.startswith("{"):
        match = re.fullmatch(r"\s*[\w.$]+\s*\((.*)\)\s*;?\s*", text, re.S)
        if not match:
            raise UpstreamError("详情响应不是可确认的 JSON/JSONP")
        body = match[1]
    try:
        raw = json.loads(body)
    except ValueError as exc:
        raise UpstreamError("详情响应无法解析") from exc

    try:
        _classify_error(raw, DETAIL_API)
    except RiskControlError as exc:
        raise RiskControlError(
            "商品详情触发了闲鱼风控（需要人工验证），本轮停止，不会自动重试或绕过",
            raw=raw,
        ) from exc

    ret = raw.get("ret")
    ret_items = ret if isinstance(ret, list) else [ret]
    if any(str(value).partition("::")[0] != "SUCCESS" for value in ret_items):
        raise GoofishError("详情响应不是明确成功状态", raw=raw)

    data = raw.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("itemDO"), dict):
        raise UpstreamError("详情响应缺少商品数据", raw=raw)
    if data.get("sellerDO") is not None and not isinstance(data["sellerDO"], dict):
        raise UpstreamError("详情响应卖家数据格式无效", raw=raw)
    item = data["itemDO"]
    for field in ("itemLabelExtList", "imageInfos"):
        if item.get(field) is not None and not isinstance(item[field], list):
            raise UpstreamError(f"详情响应 {field} 格式无效", raw=raw)
    if str(item.get("itemId") or "") != item_id:
        raise UpstreamError(f"详情响应商品标识与请求不一致（请求 {item_id}）", raw=raw)
    return raw


def _trip_on_risk(exc: GoofishError) -> None:
    """观察到的详情响应是风控时触发熔断。

    冷却等级由 `guard.classify_level` 按**原始 ret 标记**判定，所以这里把 ret 原文
    带过去，而不是用包装后的中文消息——否则等级只能落到最低档。注入兜底路径里
    「页面是验证码/安全验证页」的 blocked 分支本来就会 trip，这条补的是主链路：
    过去 `_run()` 不走 `guard.watch()`，CLI/MCP/HTTP 的错误处理也不 trip，
    于是风控提示之后后续命令还能继续打同一个账号。
    """
    if not isinstance(exc, RiskControlError):
        return
    from .guard import trip

    ret = (exc.raw or {}).get("ret") if isinstance(exc.raw, dict) else None
    items = ret if isinstance(ret, list) else ([ret] if ret else [])
    reason = " | ".join(str(value) for value in items) or str(exc)
    trip(reason, api="item.view.browser")


class ItemResponse:
    """消费本次观察到的那一个匹配请求的响应，迟到/旧响应不覆盖终态错误。"""

    def __init__(self, page: Any, item_id: str) -> None:
        self.page = page
        self.item_id = item_id
        self.results: asyncio.Queue[dict | GoofishError] = asyncio.Queue()
        self.last_error: GoofishError | None = None
        self.saw_request = False
        self.requests: set[Any] = set()
        self.tasks: set[asyncio.Task] = set()
        self.request_handler = self._request
        self.response_handler = self._schedule
        self.failure_handler = self._failed
        self.close_handler = self._closed
        page.on("request", self.request_handler)
        page.on("response", self.response_handler)
        page.on("requestfailed", self.failure_handler)
        page.on("close", self.close_handler)

    def _request(self, request: Any) -> None:
        if _matches_request(request, self.item_id):
            self.saw_request = True
            self.requests.add(request)

    def _failed(self, request: Any) -> None:
        self.requests.discard(request)

    def _closed(self) -> None:
        self.results.put_nowait(GoofishError("商品详情页已关闭"))

    def _schedule(self, response: Any) -> None:
        if response.request not in self.requests:
            return
        self.requests.discard(response.request)
        task = asyncio.create_task(self._capture(response))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _capture(self, response: Any) -> None:
        if response.status != 200:
            self.results.put_nowait(GoofishError(f"详情服务返回 HTTP {response.status}"))
            return
        # 导航可能取消响应体读取；只等页面后续的自然响应，不主动补发请求。
        try:
            text = await response.text()
        except PlaywrightError:
            self.last_error = GoofishError("详情响应读取中断，未收到后续成功响应")
            return
        try:
            result: dict | GoofishError = _decode_response(text, self.item_id)
        except GoofishError as exc:
            ret = (exc.raw or {}).get("ret") if isinstance(exc.raw, dict) else None
            ret_items = ret if isinstance(ret, list) else [ret] if ret else []
            if ret_items and all(
                str(value).partition("::")[0] in _TOKEN_REFRESH_CODES for value in ret_items
            ):
                # SDK 会自行刷新令牌并重发；中间态不代表用户会话已失效。
                self.last_error = exc
                return
            result = exc
        if isinstance(result, GoofishError):
            _trip_on_risk(result)
        self.results.put_nowait(result)

    def _timeout_error(self) -> GoofishError:
        if not self.saw_request:
            return NoDetailRequestError(
                "详情页未发出目标商品的详情请求（页面可能未水合），可用注入兜底重试"
            )
        if self.last_error is not None:
            return self.last_error
        return GoofishError("等待详情响应超时，未收到目标商品的成功详情响应")

    async def wait(self, timeout: float) -> dict:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise self._timeout_error()
            try:
                result = await asyncio.wait_for(self.results.get(), remaining)
            except TimeoutError:
                raise self._timeout_error() from None
            if isinstance(result, GoofishError):
                raise result
            return result

    async def close(self) -> None:
        for event, handler in (
            ("request", self.request_handler),
            ("response", self.response_handler),
            ("requestfailed", self.failure_handler),
            ("close", self.close_handler),
        ):
            try:
                self.page.remove_listener(event, handler)
            except Exception:  # noqa: BLE001 - 页面已销毁时 remove_listener 会抛，不能掩盖主流程
                pass
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.requests.clear()


async def read_item_page(page: Any, item_id: str, *, timeout: float = DETAIL_TIMEOUT) -> dict:
    """导航与取数共享同一截止时间；页面被替换不影响响应观察。"""
    if not math.isfinite(timeout) or timeout <= 0:
        raise GoofishError("详情读取超时必须为有限正数")
    observer = ItemResponse(page, item_id)
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        try:
            await page.goto(
                ITEM_URL.format(item_id=item_id),
                wait_until="commit",
                timeout=timeout * 1000,
            )
        except PlaywrightError as exc:
            raise GoofishError("商品详情页加载失败或超时，请检查网络与浏览器状态") from exc
        return await observer.wait(deadline - asyncio.get_running_loop().time())
    finally:
        await observer.close()


__test__ = {
    "_matches_request": _matches_request,
    "_trip_on_risk": _trip_on_risk,
    "_decode_response": _decode_response,
    "NoDetailRequestError": NoDetailRequestError,
}
