"""item 详情读取的离线契约测试：响应观察、身份校验与字段映射。

不启动浏览器，不访问闲鱼：合成的商品 ID 全为 0，避免触碰真实商品标识。
"""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from goofish_z.commands.item import view as view_mod
from goofish_z.core.errors import (
    AuthRequiredError,
    GoofishError,
    RiskControlError,
    UpstreamError,
)
from goofish_z.core.item_page import (
    DETAIL_PATH,
    ItemResponse,
    NoDetailRequestError,
    _decode_response,
    _matches_request,
    read_item_page,
)

SID = "0000000000000"


def payload(item_id: str = SID, **overrides) -> dict:
    """一条合规的详情响应（结构照 itemDO/sellerDO/itemLabelExtList）。"""
    raw = {
        "ret": ["SUCCESS::调用成功"],
        "data": {
            "itemDO": {
                "itemId": item_id,
                "title": "合成测试商品 01",
                "desc": "合成测试描述",
                "soldPrice": "123.45",
                "originalPrice": "199.00",
                "wantCnt": 7,
                "collectCnt": 3,
                "browseCnt": 41,
                "itemStatusStr": "在售",
                "itemLabelExtList": [
                    {"propertyText": "成色", "text": "95新"},
                    {"propertyText": "品牌", "text": "合成品牌"},
                    {"propertyText": "分类", "text": "其他"},
                ],
                "imageInfos": [{"url": "https://example.invalid/a.jpg"}],
            },
            "sellerDO": {
                "sellerId": "000000001",
                "nick": "合成测试卖家",
                "publishCity": "杭州",
                "xianyuSummary": "卖家信用极好",
                "replyRatio24h": "100%",
                "replyInterval": "5分钟内",
            },
        },
    }
    raw["data"].update(overrides)
    return raw


class FakeRequest:
    """Playwright 的 Request 可哈希；SimpleNamespace 含 __eq__ 会变成不可哈希。"""

    def __init__(self, url: str, post_data: str | None = None) -> None:
        self.url = url
        self.post_data = post_data


def request(url: str, post_data: str | None = None) -> FakeRequest:
    return FakeRequest(url, post_data)


def detail_request(item_id: str = SID) -> FakeRequest:
    body = "data=" + json.dumps({"itemId": item_id})
    return request(f"https://h5api.m.goofish.com{DETAIL_PATH}", body)


class FakeResponse:
    def __init__(self, req, body: dict | str | None, status: int = 200) -> None:
        self.request = req
        self.status = status
        self._body = body

    async def text(self) -> str:
        if isinstance(self._body, str):
            return self._body
        return json.dumps(self._body)


class FakePage:
    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}
        self.goto_calls: list[dict] = []
        self.goto_error: Exception | None = None

    def on(self, event: str, handler) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler) -> None:
        self.handlers.get(event, []).remove(handler)

    async def goto(self, url: str, **kwargs) -> None:
        self.goto_calls.append({"url": url, **kwargs})
        if self.goto_error is not None:
            raise self.goto_error

    def emit(self, event: str, argument=None) -> None:
        for handler in list(self.handlers.get(event, [])):
            handler() if argument is None else handler(argument)


class MatchesRequestTest(unittest.TestCase):
    def test_accepts_detail_request_for_target_item(self) -> None:
        self.assertTrue(_matches_request(detail_request(), SID))

    def test_reads_item_id_from_query_when_post_data_missing(self) -> None:
        url = f"https://h5api.m.goofish.com{DETAIL_PATH}?data={json.dumps({'itemId': SID})}"
        self.assertTrue(_matches_request(request(url, None), SID))

    def test_rejects_other_item_id(self) -> None:
        self.assertFalse(_matches_request(detail_request("0000000000001"), SID))

    def test_rejects_other_path_and_host(self) -> None:
        other_path = request("https://h5api.m.goofish.com/h5/mtop.taobao.idle.other/1.0/")
        other_host = request(f"https://example.invalid{DETAIL_PATH}")
        self.assertFalse(_matches_request(other_path, SID))
        self.assertFalse(_matches_request(other_host, SID))

    def test_rejects_unparsable_body(self) -> None:
        broken = request(f"https://h5api.m.goofish.com{DETAIL_PATH}", "data={not-json")
        self.assertFalse(_matches_request(broken, SID))


class DecodeResponseTest(unittest.TestCase):
    def test_accepts_plain_json(self) -> None:
        raw = _decode_response(json.dumps(payload()), SID)
        self.assertEqual(raw["data"]["itemDO"]["itemId"], SID)

    def test_unwraps_jsonp(self) -> None:
        body = "mtopjsonp1(" + json.dumps(payload()) + ")"
        self.assertEqual(_decode_response(body, SID)["ret"], ["SUCCESS::调用成功"])

    def test_rejects_non_json_body(self) -> None:
        with self.assertRaises(UpstreamError):
            _decode_response("<html>blocked</html>", SID)

    def test_rejects_missing_ret(self) -> None:
        with self.assertRaises(UpstreamError):
            _decode_response(json.dumps({"data": {}}), SID)

    def test_non_success_ret_is_business_error(self) -> None:
        raw = payload()
        raw["ret"] = ["FAIL_SYS_SERVICE_UNAVAILABLE::服务不可用"]
        with self.assertRaises(GoofishError) as ctx:
            _decode_response(json.dumps(raw), SID)
        self.assertNotIsInstance(ctx.exception, UpstreamError)

    def test_risk_control_ret_maps_to_risk_error(self) -> None:
        raw = payload()
        raw["ret"] = ["FAIL_SYS_USER_VALIDATE::RGV587_ERROR::SM"]
        with self.assertRaises(RiskControlError):
            _decode_response(json.dumps(raw), SID)

    def test_session_expired_maps_to_auth_error(self) -> None:
        raw = payload()
        raw["ret"] = ["FAIL_SYS_SESSION_EXPIRED::Session过期"]
        with self.assertRaises(AuthRequiredError):
            _decode_response(json.dumps(raw), SID)

    def test_rejects_missing_item_do(self) -> None:
        with self.assertRaises(UpstreamError):
            _decode_response(json.dumps({"ret": ["SUCCESS::ok"], "data": {}}), SID)

    def test_rejects_malformed_optional_fields(self) -> None:
        for field, broken in (
            ("sellerDO", ["invalid"]),
            ("itemLabelExtList", 42),
        ):
            raw = payload()
            if field == "sellerDO":
                raw["data"][field] = broken
            else:
                raw["data"]["itemDO"][field] = broken
            with self.assertRaises(UpstreamError, msg=field):
                _decode_response(json.dumps(raw), SID)

    def test_rejects_item_id_mismatch(self) -> None:
        with self.assertRaises(UpstreamError):
            _decode_response(json.dumps(payload(item_id="0000000000009")), SID)


class ItemResponseTest(unittest.IsolatedAsyncioTestCase):
    async def test_delivers_success(self) -> None:
        page = FakePage()
        observer = ItemResponse(page, SID)
        try:
            waiter = asyncio.create_task(observer.wait(1))
            req = detail_request()
            observer._request(req)
            self.assertTrue(observer.saw_request)
            observer._schedule(FakeResponse(req, payload()))
            raw = await asyncio.wait_for(waiter, 1)
            self.assertEqual(raw["data"]["itemDO"]["title"], "合成测试商品 01")
        finally:
            await observer.close()

    async def test_late_response_does_not_overwrite_terminal_error(self) -> None:
        page = FakePage()
        observer = ItemResponse(page, SID)
        try:
            session_error = payload()
            session_error["ret"] = ["FAIL_SYS_SESSION_EXPIRED::Session过期"]
            await observer._capture(FakeResponse(detail_request(), session_error))
            await observer._capture(FakeResponse(detail_request(), payload()))
            with self.assertRaises(AuthRequiredError):
                await observer.wait(0.1)
        finally:
            await observer.close()

    async def test_token_refresh_intermediate_is_not_auth_failure(self) -> None:
        page = FakePage()
        observer = ItemResponse(page, SID)
        try:
            waiter = asyncio.create_task(observer.wait(1))
            refresh = payload()
            refresh["ret"] = ["FAIL_SYS_TOKEN_EXPIRED::令牌过期"]
            await observer._capture(FakeResponse(detail_request(), refresh))
            await asyncio.sleep(0.05)
            self.assertFalse(waiter.done())
            await observer._capture(FakeResponse(detail_request(), payload()))
            raw = await asyncio.wait_for(waiter, 1)
            self.assertEqual(raw["data"]["itemDO"]["itemId"], SID)
        finally:
            await observer.close()

    async def test_repeated_risk_challenges_do_not_extend_deadline(self) -> None:
        page = FakePage()
        observer = ItemResponse(page, SID)
        challenge = payload()
        challenge["ret"] = ["FAIL_SYS_USER_VALIDATE::RGV587_ERROR::SM"]

        async def flood() -> None:
            while True:
                await observer._capture(FakeResponse(detail_request(), challenge))
                await asyncio.sleep(0)

        producer = asyncio.create_task(flood())
        try:
            with self.assertRaises(RiskControlError):
                await asyncio.wait_for(observer.wait(0.05), 0.5)
        finally:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
            await observer.close()

    async def test_http_error_surfaces(self) -> None:
        page = FakePage()
        observer = ItemResponse(page, SID)
        try:
            waiter = asyncio.create_task(observer.wait(1))
            req = detail_request()
            observer._request(req)
            observer._schedule(FakeResponse(req, None, status=503))
            with self.assertRaises(GoofishError) as ctx:
                await asyncio.wait_for(waiter, 1)
            self.assertIn("HTTP 503", str(ctx.exception))
        finally:
            await observer.close()

    async def test_no_request_observed_raises_typed_error(self) -> None:
        page = FakePage()
        observer = ItemResponse(page, SID)
        try:
            with self.assertRaises(NoDetailRequestError):
                await observer.wait(0.05)
        finally:
            await observer.close()

    async def test_close_removes_listeners(self) -> None:
        page = FakePage()
        observer = ItemResponse(page, SID)
        await observer.close()
        for event in ("request", "response", "requestfailed", "close"):
            self.assertFalse(page.handlers.get(event), event)


class ReadItemPageTest(unittest.IsolatedAsyncioTestCase):
    async def test_navigates_with_commit_and_reads_response(self) -> None:
        page = FakePage()

        async def drive() -> None:
            while "request" not in page.handlers:
                await asyncio.sleep(0)
            req = detail_request()
            page.emit("request", req)
            page.emit("response", FakeResponse(req, payload()))

        driver = asyncio.create_task(drive())
        try:
            raw = await read_item_page(page, SID, timeout=2)
        finally:
            await driver
        self.assertEqual(raw["data"]["itemDO"]["itemId"], SID)
        self.assertEqual(page.goto_calls[0]["wait_until"], "commit")
        self.assertIn(SID, page.goto_calls[0]["url"])

    async def test_navigation_failure_is_domain_error(self) -> None:
        from playwright.async_api import Error as PlaywrightError

        page = FakePage()
        page.goto_error = PlaywrightError("boom")
        with self.assertRaises(GoofishError) as ctx:
            await read_item_page(page, SID, timeout=1)
        self.assertIn("详情页加载失败", str(ctx.exception))

    async def test_rejects_non_finite_timeout(self) -> None:
        for bad in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(GoofishError, msg=repr(bad)):
                await read_item_page(FakePage(), SID, timeout=bad)


class ViewResultTest(unittest.TestCase):
    def test_maps_detail_fields(self) -> None:
        result = view_mod._view_result(payload(), SID)
        self.assertEqual(result["item_id"], SID)
        self.assertEqual(result["title"], "合成测试商品 01")
        self.assertEqual(result["price"], "¥123.45")
        self.assertEqual(result["condition"], "95新")
        self.assertEqual(result["brand"], "合成品牌")
        self.assertEqual(result["location"], "杭州")
        self.assertEqual(result["seller_name"], "合成测试卖家")
        self.assertEqual(result["want_count"], "7")
        self.assertEqual(result["image_count"], "1")
        self.assertEqual(result["image_urls"], ["https://example.invalid/a.jpg"])
        self.assertIn("userId=000000001", result["seller_url"])

    def test_missing_title_is_error(self) -> None:
        raw = payload()
        raw["data"]["itemDO"]["title"] = ""
        with self.assertRaises(GoofishError):
            view_mod._view_result(raw, SID)

    def test_missing_price_stays_empty(self) -> None:
        raw = payload()
        raw["data"]["itemDO"].pop("soldPrice")
        self.assertEqual(view_mod._view_result(raw, SID)["price"], "")


class FallbackTest(unittest.IsolatedAsyncioTestCase):
    async def test_injects_only_when_page_never_sent_request(self) -> None:
        page = FakePage()

        class CM:
            async def __aenter__(self):
                return page

            async def __aexit__(self, *exc):
                return False

        calls: list[str] = []

        async def fake_read(p, item_id, *, timeout=20):
            calls.append("read")
            if len(calls) == 1:
                raise NoDetailRequestError("未水合")
            return payload()

        async def fake_inject(p, item_id):
            calls.append("inject")

        with (
            patch.object(view_mod, "goofish_page", lambda: CM()),
            patch.object(view_mod, "read_item_page", fake_read),
            patch.object(view_mod, "_fetch_via_page_mtop", fake_inject),
        ):
            result = await view_mod._run(SID)
        self.assertEqual(calls, ["read", "inject", "read"])
        self.assertEqual(result["item_id"], SID)

    async def test_no_injection_when_response_was_observed(self) -> None:

        class CM:
            async def __aenter__(self):
                return FakePage()

            async def __aexit__(self, *exc):
                return False

        inject = AsyncMock()
        with (
            patch.object(view_mod, "goofish_page", lambda: CM()),
            patch.object(view_mod, "read_item_page", AsyncMock(side_effect=GoofishError("读取中断"))),
            patch.object(view_mod, "_fetch_via_page_mtop", inject),
        ):
            with self.assertRaises(GoofishError) as ctx:
                await view_mod._run(SID)
        self.assertIn("读取中断", str(ctx.exception))
        inject.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
