"""Offline entry-path regressions for bounded, best-effort SKU enrichment."""
import asyncio
import importlib
import json
import inspect
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from support import OfflineCase, fixture
from goofish_z.core.errors import AuthRequiredError, BlockedError, RateLimitedError, RiskControlError
from goofish_z.mcp_server import make_handler

SKUS = [{"sku_id": "synthetic-sku", "name": "synthetic option", "price": 1000, "quantity": 1}]


class SearchEnrichmentTest(OfflineCase):
    def setUp(self):
        super().setUp()
        self.item_get = importlib.import_module("goofish_z.commands.item.get")
        self.now = 100.0

    def _items(self, count=3, title="合成多规格显卡", price="1000"):
        return [fixture(price, item_id=f"synthetic-{i}", title=title) for i in range(count)]

    @contextmanager
    def _entry(self, items, detail=None):
        payload = {"items": items, "page": 1, "has_next": False, "source_count": len(items)}
        def sleep(delay):
            self.now += delay
        with (
            patch.object(self.search, "_run", AsyncMock(return_value=payload)),
            patch.object(self.search, "filter_search_items", return_value=(items, [])),
            patch("time.time", side_effect=lambda: self.now),
            patch("time.sleep", side_effect=sleep) as wait,
            patch.object(self.item_get, "call", return_value={"data": {"itemDO": {"skuList": []}}}),
            patch.object(self.item_get.Session, "load", return_value=object()) as load,
        ):
            if detail is None:
                yield wait, load
            else:
                with patch.object(self.item_get, "_get", side_effect=detail) as get:
                    yield wait, get

    def _search(self, **kwargs):
        return self.search.search("显卡", filter_blacklist=False, resolve_skus=True, **kwargs)

    def test_three_candidates_use_real_detail_limiter_without_silent_drops(self):
        with self._entry(self._items()) as (wait, load):
            result = self._search()
        self.assertEqual(load.call_count, 3)
        self.assertEqual(wait.call_count, 2)
        self.assertGreaterEqual(sum(c.args[0] for c in wait.call_args_list), 10)
        status = result["sku_resolution"]
        self.assertTrue(status["complete"])
        self.assertEqual(status["attempted"], 3)
        self.assertEqual(status["without_skus"], 3)

    def test_resolve_skus_is_independent_of_low_value_filter(self):
        with self._entry(self._items(1), detail=lambda _, **kwargs: {"skus": SKUS}) as (_, get):
            result = self._search(filter_low_value=False)
        get.assert_called_once()
        self.assertEqual(result["items"][0]["skus"], SKUS)
        self.assertTrue(result["sku_resolution"]["complete"])
        self.assertNotIn("vmi", result["items"][0])

    def test_broad_query_never_uses_invented_price_reference(self):
        items = self._items(1, title="合成普通显卡", price="600")
        with self._entry(items, detail=lambda _, **kwargs: {"skus": SKUS}) as (_, get):
            result = self._search()
        get.assert_not_called()
        self.assertEqual(result["sku_resolution"]["candidates"], 0)
        self.assertTrue(result["sku_resolution"]["complete"])

    def test_actual_specific_model_median_still_selects_cheap_candidate(self):
        items = self._items(title="合成RTX3090 正常使用")
        items[0]["price"] = "600"
        with self._entry(items, detail=lambda _, **kwargs: {"skus": SKUS}) as (_, get):
            result = self.search.search("RTX3090", filter_blacklist=False,
                                        resolve_skus=True, filter_low_value=False)
        get.assert_called_once_with("synthetic-0", auto_refresh=False)
        self.assertEqual(result["sku_resolution"]["resolved"], 1)

    def test_auth_risk_and_rate_failures_stop_further_details(self):
        for error, reason in ((AuthRequiredError("synthetic private detail"), "auth_required"),
                              (RiskControlError("synthetic private detail"), "risk_control"),
                              (BlockedError("synthetic private detail"), "risk_control"),
                              (RateLimitedError("synthetic private detail"), "rate_limited")):
            with self.subTest(reason=reason):
                self.limiter.STATE_PATH.unlink(missing_ok=True)
                with self._entry(self._items(), detail=lambda _, **kwargs: (_ for _ in ()).throw(error)) as (_, get):
                    result = self._search(filter_low_value=False)
                self.assertEqual(get.call_count, 1)
                self.assertEqual(result["count"], 3)
                status = result["sku_resolution"]
                self.assertFalse(status["complete"])
                self.assertEqual(status["stop_reason"], reason)
                self.assertEqual(status["skipped"], 2)
                self.assertNotIn("synthetic private detail", json.dumps(result))

    def test_generic_detail_failure_is_visible_but_keeps_best_effort(self):
        responses = iter([RuntimeError("synthetic private payload"), {"skus": SKUS}, {"skus": []}])
        def detail(_, **kwargs):
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response
        with self._entry(self._items(), detail=detail) as (_, get):
            result = self._search(filter_low_value=False)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(result["count"], 3)
        status = result["sku_resolution"]
        self.assertFalse(status["complete"])
        self.assertIsNone(status["stop_reason"])
        self.assertEqual(status["failed"], 1)
        self.assertEqual(status["resolved"], 1)
        self.assertEqual(status["without_skus"], 1)
        self.assertNotIn("synthetic private payload", json.dumps(result))

    def test_detail_budget_is_reported_as_incomplete(self):
        with self._entry(self._items(5), detail=lambda _, **kwargs: {"skus": SKUS}) as (_, get):
            result = self._search(filter_low_value=False)
        self.assertEqual(get.call_count, 3)
        status = result["sku_resolution"]
        self.assertEqual(status["stop_reason"], "detail_budget")
        self.assertEqual(status["skipped"], 2)
        self.assertFalse(status["complete"])

    def test_long_local_rate_wait_is_bounded_without_request(self):
        with patch.dict("os.environ", {"GOOFISH_LIMIT_DETAIL_SEC": "60"}):
            with self._entry(self._items()) as (wait, load):
                self.limiter.check("detail")
                result = self._search(filter_low_value=False)
        wait.assert_not_called()
        load.assert_not_called()
        self.assertEqual(result["sku_resolution"]["stop_reason"], "wait_budget")
        self.assertEqual(result["sku_resolution"]["attempted"], 0)

    def test_guard_rechecked_after_local_wait_prevents_request(self):
        def trip_while_waiting(delay):
            self.now += delay
            self.guard.trip("synthetic risk")
        with self._entry(self._items()) as (wait, load):
            self.limiter.check("detail")
            wait.side_effect = trip_while_waiting
            result = self._search(filter_low_value=False)
        load.assert_not_called()
        self.assertEqual(result["sku_resolution"]["stop_reason"], "risk_control")
        self.assertEqual(result["sku_resolution"]["attempted"], 0)

    def test_opt_out_does_not_add_metadata_or_details(self):
        with self._entry(self._items(), detail=lambda _, **kwargs: {"skus": SKUS}) as (_, get):
            result = self.search.search("显卡", filter_blacklist=False, resolve_skus=False)
        get.assert_not_called()
        self.assertNotIn("sku_resolution", result)

    def test_json_cli_mcp_and_http_keep_items_and_partial_metadata(self):
        from goofish_z.cli import app
        api = importlib.import_module("goofish_z.api.app")
        def denied(_, **kwargs):
            raise AuthRequiredError("synthetic expired")
        with self._entry(self._items(), detail=denied):
            cli = CliRunner().invoke(app, ["search", "items", "显卡", "--resolve-skus", "--format", "json"])
        self.assertEqual(cli.exit_code, 0, cli.output)
        cli_result = json.loads(cli.stdout)
        self.limiter.STATE_PATH.unlink(missing_ok=True)
        with self._entry(self._items(), detail=denied):
            mcp = asyncio.run(make_handler(self.search.search)("显卡", resolve_skus=True))
        self.limiter.STATE_PATH.unlink(missing_ok=True)
        with self._entry(self._items(), detail=denied):
            http = TestClient(api.app).get("/api/search", params={"q": "显卡", "resolve_skus": "true"})
        self.assertEqual(http.status_code, 200)
        for result in (cli_result, mcp, http.json()):
            self.assertEqual(len(result["items"]), 3)
            self.assertFalse(result["sku_resolution"]["complete"])
            self.assertEqual(result["sku_resolution"]["stop_reason"], "auth_required")

    def test_shared_rate_race_stops_without_retrying_detail(self):
        real_get = self.item_get._get
        def competing_request(item_id, **kwargs):
            self.limiter.check("detail")
            return real_get(item_id, **kwargs)
        with self._entry(self._items(), detail=competing_request) as (_, get):
            result = self._search(filter_low_value=False)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(result["sku_resolution"]["stop_reason"], "rate_limited")
        self.assertEqual(result["sku_resolution"]["skipped"], 2)

    def test_wait_budget_caps_total_sleep_not_just_each_wait(self):
        with patch.dict("os.environ", {"GOOFISH_LIMIT_DETAIL_SEC": "8"}):
            with self._entry(self._items()) as (wait, load):
                result = self._search(filter_low_value=False)
        self.assertEqual(load.call_count, 2)
        self.assertEqual(wait.call_count, 1)
        self.assertLessEqual(sum(call.args[0] for call in wait.call_args_list), 12)
        self.assertEqual(result["sku_resolution"]["stop_reason"], "wait_budget")
        self.assertFalse(result["sku_resolution"]["complete"])

    def test_missing_candidate_id_is_visible_without_request(self):
        item = self._items(1)[0]
        item.pop("item_id")
        with self._entry([item], detail=lambda _, **kwargs: {"skus": SKUS}) as (_, get):
            result = self._search(filter_low_value=False)
        get.assert_not_called()
        self.assertFalse(result["sku_resolution"]["complete"])
        self.assertEqual(result["sku_resolution"]["failures"], [{"item_id": "", "reason": "missing_id"}])

    def test_real_mtop_auth_failure_never_recovers_or_retries(self):
        mtop = importlib.import_module("goofish_z.core.mtop")
        session = MagicMock(h5_token="synthetic-token")
        session.http.post.return_value.status_code = 200
        session.http.post.return_value.json.return_value = {"ret": ["FAIL_SYS_TOKEN_EXOIRED::expired"]}
        with (
            self._entry(self._items()) as (_, load),
            patch.object(self.item_get, "call", mtop.call),
            patch.object(mtop, "generate_sign", return_value="synthetic-sign"),
            patch("goofish_z.core.refresh.is_enabled", return_value=True),
            patch("goofish_z.core.refresh.refresh_cookies_via_browser") as browser,
        ):
            load.return_value = session
            result = self._search(filter_low_value=False)
        load.assert_called_once_with(allow_bootstrap=False)
        self.assertEqual(session.http.post.call_count, 1)
        session.refresh_token.assert_not_called()
        browser.assert_not_called()
        self.assertEqual(result["sku_resolution"]["stop_reason"], "auth_required")

    def test_real_mtop_rate_failure_stops_batch(self):
        mtop = importlib.import_module("goofish_z.core.mtop")
        for code, payload in ((429, None), (200, {"ret": ["FAIL_SYS_TRAFFIC_LIMIT::slow down"]})):
            with self.subTest(code=code):
                self.limiter.STATE_PATH.unlink(missing_ok=True)
                session = MagicMock(h5_token="synthetic-token")
                session.http.post.return_value.status_code = code
                session.http.post.return_value.headers = {"Retry-After": "5"}
                session.http.post.return_value.json.return_value = payload
                with (
                    self._entry(self._items()) as (_, load),
                    patch.object(self.item_get, "call", mtop.call),
                    patch.object(mtop, "generate_sign", return_value="synthetic-sign"),
                ):
                    load.return_value = session
                    result = self._search(filter_low_value=False)
                self.assertEqual(session.http.post.call_count, 1)
                session.refresh_token.assert_not_called()
                self.assertEqual(result["sku_resolution"]["stop_reason"], "rate_limited")

    def test_public_item_get_signature_and_recovery_policy_unchanged(self):
        self.assertEqual(list(inspect.signature(self.item_get.get).parameters), ["item_id"])
        with patch.object(self.item_get, "_get", return_value={"synthetic": True}) as read:
            self.assertEqual(self.item_get.get("synthetic-id"), {"synthetic": True})
        read.assert_called_once_with("synthetic-id")
        with self._entry(self._items()) as (_, load), patch.object(self.item_get, "call", return_value={}) as call:
            self.item_get.get("synthetic-id")
        load.assert_called_once_with(allow_bootstrap=True)
        self.assertIs(call.call_args.kwargs["_auto_refresh"], True)
