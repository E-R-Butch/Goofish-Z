"""上游响应契约与刷新风控熔断。

对应两条 P0：
- mtop._classify_error 曾把「缺 ret / ret 为空 / 非 JSON 对象」当成功放行；
- Session.refresh_token 遇到风控只返回 False，上层随即走浏览器刷新重试，账号
  受限期间继续打请求，且从不触发熔断。
"""

import importlib
from unittest.mock import MagicMock, patch

from support import OfflineCase


class UpstreamContractTest(OfflineCase):
    def setUp(self):
        super().setUp()
        self.mtop = importlib.import_module("goofish_z.core.mtop")
        self.session_mod = importlib.import_module("goofish_z.core.session")
        self.errors = importlib.import_module("goofish_z.core.errors")

    # ---------- ret 契约 ----------

    def test_missing_ret_is_not_treated_as_success(self):
        with self.assertRaises(self.errors.UpstreamError):
            self.mtop._classify_error({}, "api.test")

    def test_non_object_payload_is_rejected(self):
        for payload in (None, [], "oops", 42):
            with self.subTest(payload=payload), self.assertRaises(self.errors.UpstreamError):
                self.mtop._classify_error(payload, "api.test")

    def test_empty_ret_and_null_items_are_rejected(self):
        for payload in ({"ret": []}, {"ret": [None]}, {"ret": ""}):
            with self.subTest(payload=payload), self.assertRaises(self.errors.UpstreamError):
                self.mtop._classify_error(payload, "api.test")

    def test_non_string_ret_elements_are_rejected(self):
        for payload in (
            {"ret": {"status": "SUCCESS"}},
            {"ret": [{"status": "SUCCESS"}]},
            {"ret": [123]},
        ):
            with self.subTest(payload=payload), self.assertRaises(self.errors.UpstreamError):
                self.mtop._classify_error(payload, "api.test")

    def test_success_still_passes(self):
        self.mtop._classify_error({"ret": ["SUCCESS::调用成功"]}, "api.test")

    def test_string_ret_is_accepted(self):
        self.mtop._classify_error({"ret": "SUCCESS::调用成功"}, "api.test")

    def test_risk_ret_raises_risk_control(self):
        with self.assertRaises(self.errors.RiskControlError):
            self.mtop._classify_error({"ret": ["RGV587_ERROR::SM::哎哟喂,被挤爆啦"]}, "api.test")

    # ---------- refresh_token 风控熔断 ----------

    def _session_with(self, payload):
        # refresh_token 会先算一次 mtop 签名；签名走 JS 运行时，单元测试不该依赖 node。
        self.use(patch.object(self.session_mod, "generate_sign", return_value="synthetic-sign"))
        session = self.session_mod.Session(
            http=MagicMock(),
            unb="synthetic-unb",
            tracknick="",
            device_id="synthetic-device",
        )
        # h5_token property 与 unb 校验都走 cookies
        session.http.cookies.get.return_value = "synthetic-token"
        response = MagicMock()
        response.cookies = []
        response.json.return_value = payload
        session.http.post.return_value = response
        return session

    def test_refresh_token_trips_guard_on_risk(self):
        session = self._session_with({"ret": ["RGV587_ERROR::SM"]})
        with self.assertRaises(self.errors.RiskControlError):
            session.refresh_token()
        state = self.guard.status()
        self.assertTrue(state["tripped"])
        self.assertEqual(state["level"], "hard")

    def test_refresh_token_failure_without_risk_does_not_trip(self):
        session = self._session_with({"ret": ["FAIL_SYS_TRAFFIC_LIMIT::限流"]})
        self.assertFalse(session.refresh_token())
        self.assertFalse(self.guard.status()["tripped"])

    def test_refresh_token_success_returns_true(self):
        session = self._session_with({"ret": ["SUCCESS::调用成功"]})
        self.assertTrue(session.refresh_token())
        self.assertFalse(self.guard.status()["tripped"])
