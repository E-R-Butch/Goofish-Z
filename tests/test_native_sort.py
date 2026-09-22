import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlencode

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from support import OfflineCase, fixture
from goofish_z.core.errors import AuthRequiredError, GoofishError, RiskControlError
from goofish_z.core.search_sort import (
    SORT_OPTIONS, is_search_response, observe_sorted_action, request_metadata,
    response_item_ids, select_native_sort, verify_sorted_items,
)


def response(sort="price_asc", page=1, ids=("101", "102"), **changes):
    field, value, _ = SORT_OPTIONS[sort]
    data = {"keyword": "synthetic", "sortField": field, "sortValue": value, "pageNumber": page,
            "gps": "private synthetic value"}
    data.update(changes)
    request = SimpleNamespace(url="https://h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.search/1.0/", post_data=urlencode({"data": json.dumps(data)}))
    raw = {"ret": ["SUCCESS::调用成功"], "data": {"resultList": [
        {"data": {"item": {"main": {"exContent": {"itemId": item_id}}}}} for item_id in ids
    ]}}
    return SimpleNamespace(url=request.url, request=request, status=200, json=AsyncMock(return_value=raw))


def payload(ids, sort="price_asc", page=1):
    return {"items": [fixture(item_id=i, url=f"https://www.goofish.com/item?id={i}") for i in ids],
            "source_query": "synthetic", "source_sort": sort, "page": page, "has_next": True}


class NativeSortTest(OfflineCase):
    def test_response_checks_sort_query_and_page_without_exposing_other_metadata(self):
        r = response()
        self.assertEqual(set(request_metadata(r.request)), {"keyword", "sortField", "sortValue", "pageNumber"})
        self.assertTrue(is_search_response(r, "synthetic"))
        self.assertEqual(asyncio.run(response_item_ids(r, "synthetic", "price_asc", 1)), ["101", "102"])
        for changes in ({"keyword": "old"}, {"sortValue": "desc"}, {"pageNumber": 2}, {"sortField": "create"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(GoofishError, "不一致"):
                asyncio.run(response_item_ids(response(**changes), "synthetic", "price_asc", 1))

    def test_failed_and_unrecognizable_responses_are_not_claimed_as_sorted(self):
        for raw, error in [
            ({"ret": ["FAIL_SYS_SESSION_EXPIRED"]}, AuthRequiredError),
            ({"ret": ["FAIL_SYS_USER_VALIDATE"]}, RiskControlError),
            ({"ret": [], "data": {}}, GoofishError),
            ({"ret": ["SUCCESS::ok"], "data": {"resultList": [{}]}}, GoofishError),
        ]:
            r = response(); r.json.return_value = raw
            with self.subTest(raw=raw), self.assertRaises(error):
                asyncio.run(response_item_ids(r, "synthetic", "price_asc", 1))

    def test_stale_rendered_ids_or_sort_are_rejected_even_if_prices_look_sorted(self):
        for data in (payload(["102", "101"]), payload(["101"], "price_desc"), payload([])):
            with self.subTest(data=data), self.assertRaises(GoofishError):
                verify_sorted_items(data, ["101", "102"], "price_asc")
        verify_sorted_items(payload(["101"]), ["101", "102"], "price_asc")
        verify_sorted_items(payload([]), [], "price_asc")

    def test_sort_action_observes_native_response_before_waiting_for_render(self):
        page = MagicMock(); page.wait_for_function = AsyncMock()
        @asynccontextmanager
        async def receive(predicate, timeout):
            r = response()
            self.assertTrue(predicate(r))
            yield SimpleNamespace(value=AsyncMock(return_value=r)())
        page.expect_response = receive
        action = AsyncMock()
        self.assertEqual(asyncio.run(observe_sorted_action(page, action, "synthetic", "price_asc", 1)), ["101", "102"])
        action.assert_awaited_once()
        self.assertEqual(page.wait_for_function.call_args.kwargs["arg"]["label"], "价格从低到高")

    def test_missing_sort_control_fails_instead_of_silently_using_default(self):
        from playwright.async_api import TimeoutError
        page = MagicMock()
        page.locator.return_value.filter.return_value.click = AsyncMock(side_effect=TimeoutError("synthetic"))
        slot = AsyncMock()
        with self.assertRaisesRegex(GoofishError, "未退回默认排序"):
            asyncio.run(select_native_sort(page, "synthetic", "price_asc", slot))
        slot.assert_not_awaited()

    def test_native_sort_survives_pagination_and_returns_source_order(self):
        page = MagicMock(); page.goto = AsyncMock()
        @asynccontextmanager
        async def browser(): yield page
        with patch.object(self.search, "goofish_page", browser), \
             patch.object(self.search, "_read_search_page", AsyncMock(side_effect=[payload(["900"], "default"), payload(["101", "102"]), payload(["201", "202"], page=2)])), \
             patch.object(self.search, "select_native_sort", AsyncMock(return_value=["101", "102"])) as select, \
             patch.object(self.search, "_go_to_page", AsyncMock(return_value=["201", "202"])) as next_page:
            result = asyncio.run(self.search._run("synthetic", 20, 2, "price_asc"))
        self.assertEqual(result["sort"], "price_asc")
        self.assertEqual([i["item_id"] for i in result["items"]], ["201", "202"])
        next_page.assert_awaited_once_with(page, 2, "synthetic", "price_asc")
        self.assertEqual(select.await_count, 1)

    def test_invalid_sort_is_rejected_before_any_network_reservation(self):
        with patch("goofish_z.core.limiter.check") as limiter, patch.object(self.search, "_run") as run:
            with self.assertRaises(ValueError): self.search.search("synthetic", sort="unknown")
        limiter.assert_not_called(); run.assert_not_called()

    def test_http_cli_and_mcp_forward_native_sort_to_shared_search(self):
        import importlib
        from goofish_z.cli import app
        from goofish_z.mcp_server import make_handler
        api = importlib.import_module("goofish_z.api.app")
        with patch.object(self.search, "_run", AsyncMock(return_value={"items": [], "sort": "price_desc", "page": 2})) as run, \
             patch("goofish_z.core.limiter.check"), TestClient(api.app) as client:
            http = client.get("/api/search", params={"q": "synthetic", "page": 2, "sort": "price_desc"})
            self.assertEqual(http.status_code, 200)
            cli = CliRunner().invoke(app, ["search", "items", "synthetic", "--page", "2", "--sort", "price_desc", "--format", "json"])
            self.assertEqual(cli.exit_code, 0, cli.output)
            mcp = asyncio.run(make_handler(self.search.search)(query="synthetic", page=2, sort="price_desc"))
            self.assertEqual(http.json()["sort"], json.loads(cli.stdout)["sort"])
            self.assertEqual(mcp["sort"], "price_desc")
            for args in run.call_args_list: self.assertEqual(args.kwargs, {"sort": "price_desc"})
            self.assertEqual(client.get("/api/search", params={"q":"synthetic", "sort":"invalid"}).status_code, 422)
