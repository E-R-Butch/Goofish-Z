"""Rate limits during real token recovery must stop the public request chain."""

import importlib
import json
import time
from email.utils import formatdate
from unittest.mock import MagicMock, patch

import requests
from fastapi.testclient import TestClient
from support import OfflineCase

from goofish_z.core import mtop
from goofish_z.core import session as session_mod


class RefreshRateLimitTest(OfflineCase):
    def setUp(self):
        super().setUp()
        self.api = importlib.import_module("goofish_z.api.app")
        self.cookie_path = self.root / "cookies.json"
        self.cookie_path.write_text(json.dumps({
            "unb": "synthetic-account", "_m_h5_tk": "synthetic-token_0",
            "cookie2": "synthetic-cookie",
        }))
        self.before = self.cookie_path.read_bytes()
        http = requests.Session()
        http.cookies.update(json.loads(self.cookie_path.read_text()))
        self.session = session_mod.Session(
            http=http, unb="synthetic-account", tracknick="",
            device_id="synthetic-device", cookie_path=self.cookie_path,
        )
        self.use(patch.object(session_mod.Session, "load", return_value=self.session))
        self.use(patch.object(session_mod, "generate_sign", return_value="synthetic-sign"))
        self.use(patch.object(mtop, "generate_sign", return_value="synthetic-sign"))
        self.use(patch("goofish_z.core.refresh.is_enabled", return_value=True))
        self.browser = self.use(patch(
            "goofish_z.core.refresh.refresh_cookies_via_browser", return_value=False,
        ))

    def _request(self, status_code, payload, retry_after, expected_delay):
        expired = MagicMock(status_code=200, headers={})
        expired.cookies = requests.cookies.RequestsCookieJar()
        expired.json.return_value = {"ret": ["FAIL_SYS_TOKEN_EXOIRED::expired"]}
        limited = MagicMock(status_code=status_code, headers={"Retry-After": retry_after})
        limited.cookies = requests.cookies.RequestsCookieJar()
        if payload is None:
            limited.json.side_effect = ValueError("synthetic HTML rate limit")
        else:
            limited.json.return_value = payload
        with patch.object(self.session.http, "post", side_effect=[expired, limited]) as post:
            response = TestClient(self.api.app).get("/api/item/get?item_id=123456789")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], expected_delay)
        self.assertEqual(post.call_count, 2)
        self.assertIn("loginuser.get", post.call_args_list[1].args[0])
        self.browser.assert_not_called()
        self.assertFalse(self.guard.status()["tripped"])
        self.assertEqual(self.cookie_path.read_bytes(), self.before)
        if status_code == 429:
            limited.json.assert_not_called()
        return response

    def test_http_429_html_stops_recovery_and_preserves_retry_after(self):
        self._request(429, None, "600", "600")

    def test_http_429_fractional_delay_rounds_up(self):
        self._request(429, None, "12.0001", "13")

    def test_http_429_date_delay_is_preserved(self):
        now = int(time.time())
        with patch.object(mtop.time, "time", return_value=now):
            self._request(429, None, formatdate(now + 600, usegmt=True), "600")

    def test_http_429_invalid_delay_uses_safe_fallback(self):
        self._request(429, None, "nan", "1")

    def test_traffic_limit_envelope_stops_recovery_and_preserves_delay(self):
        self._request(200, {"ret": ["FAIL_SYS_TRAFFIC_LIMIT::slow down"]}, "600", "600")

    def test_traffic_limit_without_delay_stops_recovery(self):
        self._request(200, {"ret": ["FAIL_SYS_TRAFFIC_LIMIT::slow down"]}, None, "1")
