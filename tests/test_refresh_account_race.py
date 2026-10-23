"""Offline regressions: a later explicit login always wins over stale refreshes."""
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from goofish_z.core import browser_cookie, guard, refresh, session
from goofish_z.core.errors import SessionChangedError
from goofish_z.core.state_file import file_version

login = importlib.import_module("goofish_z.commands.auth.login")


def cookies(account="synthetic-A", token="synthetic-old", *, expired=False):
    return [{"name": name, "value": value, "domain": ".goofish.com", "path": "/",
             **({"expires": 1} if expired else {})}
            for name, value in {"unb": account, "_m_h5_tk": token + "_0",
                                "cookie2": token + "-session"}.items()]


class RefreshAccountRaceTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="goofish-auth-race-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "cookies.json"
        for patcher in (
            patch.dict(os.environ, {"GOOFISH_Z_DATA": str(self.root),
                                    "GOOFISH_AUTO_REFRESH_TOKEN": "1",
                                    "GOOFISH_NO_CHROME_BOOTSTRAP": "0"}),
            patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden")),
            patch.object(session, "generate_device_id", return_value="synthetic-device"),
            patch.object(session, "generate_sign", return_value="synthetic-sign"),
            patch.object(guard, "STATE_PATH", self.root / "circuit.json"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        session.write_cookies_json(self.path, cookies())
        self.loaded = session.Session.load(self.path, allow_bootstrap=False)

    def import_login(self, account="synthetic-B", token="synthetic-new"):
        # Exercise the real public import writer, not a mock persistence function.
        with patch.object(login, "_pull_from_browser", return_value=(cookies(account, token), "synthetic")):
            result = login.login(browser="chrome")
        self.assertEqual(result["unb"], account)
        return self.path.read_bytes()

    def test_browser_refresh_cannot_overwrite_newer_account_or_same_account_login(self):
        for account in ("synthetic-B", "synthetic-A"):
            with self.subTest(account=account):
                session.write_cookies_json(self.path, cookies())
                loaded = session.Session.load(self.path, allow_bootstrap=False)
                expected = []

                async def in_flight(_current):
                    expected.append(self.import_login(account))
                    return cookies(token="synthetic-refreshed")

                with patch.object(refresh, "_refresh_async", side_effect=in_flight):
                    with self.assertRaises(SessionChangedError):
                        refresh.refresh_cookies_via_browser(loaded)
                self.assertEqual(self.path.read_bytes(), expected[0])
                self.assertEqual(loaded.http.cookies.get("_m_h5_tk"), "synthetic-old_0")

    def test_http_refresh_cannot_merge_old_tokens_into_a_newer_login(self):
        for account in ("synthetic-B", "synthetic-A"):
            with self.subTest(account=account):
                session.write_cookies_json(self.path, cookies())
                loaded = session.Session.load(self.path, allow_bootstrap=False)
                expected = []

                def in_flight(*args, **kwargs):
                    expected.append(self.import_login(account))
                    jar = requests.cookies.RequestsCookieJar()
                    # Real refresh responses need not repeat the account ID.
                    jar.set("_m_h5_tk", "synthetic-refreshed_0", domain=".goofish.com", path="/")
                    return SimpleNamespace(status_code=200, headers={}, cookies=jar, json=lambda: {"ret": ["SUCCESS::ok"]})

                with patch.object(loaded.http, "post", side_effect=in_flight):
                    with self.assertRaises(SessionChangedError):
                        loaded.refresh_token()
                self.assertEqual(self.path.read_bytes(), expected[0])

    def test_old_loaded_session_stops_before_any_refresh_io(self):
        self.import_login("synthetic-A")
        with patch.object(refresh, "_refresh_async") as browser, patch.object(self.loaded.http, "post") as post:
            with self.assertRaises(SessionChangedError):
                refresh.refresh_cookies_via_browser(self.loaded)
            with self.assertRaises(SessionChangedError):
                self.loaded.refresh_token()
        browser.assert_not_called()
        post.assert_not_called()

    def test_browser_refresh_does_not_accept_a_different_account(self):
        original = self.path.read_bytes()
        async def wrong_account(_current):
            return cookies("synthetic-B")
        with patch.object(refresh, "_refresh_async", side_effect=wrong_account):
            with self.assertRaises(SessionChangedError):
                refresh.refresh_cookies_via_browser(self.loaded)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.loaded.http.cookies.get("unb"), "synthetic-A")

    def test_same_account_browser_refreshes_can_continue_with_updated_revision(self):
        other_origin = [{"name": "sgcookie", "value": "synthetic-other-origin", "domain": ".taobao.com", "path": "/"}]
        session.write_cookies_json(self.path, cookies() + other_origin)
        loaded = session.Session.load(self.path, allow_bootstrap=False)
        for token in ("synthetic-refresh-1", "synthetic-refresh-2"):
            async def successful(_current):
                return cookies(token=token)
            with patch.object(refresh, "_refresh_async", side_effect=successful):
                self.assertTrue(refresh.refresh_cookies_via_browser(loaded))
            self.assertEqual(loaded.http.cookies.get("_m_h5_tk"), token + "_0")
            self.assertEqual(session._load_cookies(self.path)["_m_h5_tk"], token + "_0")
            self.assertEqual(loaded.cookie_version, file_version(self.path))
            self.assertIn("synthetic-other-origin", self.path.read_text())
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_expired_cookie_identity_does_not_override_current_account(self):
        expired = [{"name": "unb", "value": "synthetic-B", "domain": "h5api.m.goofish.com",
                    "path": "/", "expires": 1}]
        session.write_cookies_json(self.path, cookies() + expired)
        loaded = session.Session.load(self.path, allow_bootstrap=False)
        async def successful(_current):
            return cookies(token="synthetic-refreshed")
        with patch.object(refresh, "_refresh_async", side_effect=successful):
            self.assertTrue(refresh.refresh_cookies_via_browser(loaded))
        self.assertEqual(session._load_cookies(self.path)["unb"], "synthetic-A")

    def test_same_account_http_refresh_updates_snapshot(self):
        for token in ("synthetic-refresh-1", "synthetic-refresh-2"):
            jar = requests.cookies.RequestsCookieJar()
            jar.set("_m_h5_tk", token + "_0", domain=".goofish.com", path="/")
            response = SimpleNamespace(status_code=200, headers={}, cookies=jar, json=lambda: {"ret": ["SUCCESS::ok"]})
            with patch.object(self.loaded.http, "post", return_value=response):
                self.assertTrue(self.loaded.refresh_token())
            self.assertEqual(self.loaded.cookie_version, file_version(self.path))
            self.assertEqual(session._load_cookies(self.path)["_m_h5_tk"], token + "_0")

    def test_in_memory_refresh_also_stops_if_new_login_arrives(self):
        expected = []
        async def in_flight(_current):
            expected.append(self.import_login())
            return cookies(token="synthetic-refreshed")
        with patch.object(refresh, "_refresh_async", side_effect=in_flight):
            with self.assertRaises(SessionChangedError):
                refresh.refresh_cookies_via_browser(self.loaded, persist=False)
        self.assertEqual(self.path.read_bytes(), expected[0])

    def test_bootstrap_cannot_overwrite_login_during_browser_extraction(self):
        for initial in ("expired", "missing"):
            with self.subTest(initial=initial):
                if initial == "expired":
                    session.write_cookies_json(self.path, cookies(expired=True))
                else:
                    self.path.unlink()
                expected = []
                def extracting(**kwargs):
                    expected.append(self.import_login())
                    return "chrome", cookies(token="synthetic-bootstrap")
                with patch.object(browser_cookie, "extract_goofish_cookies", side_effect=extracting):
                    with self.assertRaises(SessionChangedError):
                        session._load_or_bootstrap_cookies(self.path)
                self.assertEqual(self.path.read_bytes(), expected[0])

    def test_load_pairs_cookie_values_and_revision_after_concurrent_import(self):
        original_load = session._load_or_bootstrap_cookies
        def importing(*args, **kwargs):
            result = original_load(*args, **kwargs)
            self.import_login()
            return result
        with patch.object(session, "_load_or_bootstrap_cookies", side_effect=importing):
            loaded = session.Session.load(self.path, allow_bootstrap=False)
        self.assertEqual(loaded.unb, "synthetic-B")
        self.assertEqual(loaded.cookie_version, file_version(self.path))

    def test_stale_http_refresh_does_not_fall_back_to_browser(self):
        from goofish_z.core import mtop
        expired = SimpleNamespace(status_code=200, headers={},
                                  json=lambda: {"ret": ["FAIL_SYS_SESSION_EXPIRED::expired"]})
        jar = requests.cookies.RequestsCookieJar()
        jar.set("_m_h5_tk", "synthetic-refreshed_0", domain=".goofish.com", path="/")
        def stale_refresh(*args, **kwargs):
            self.import_login()
            return SimpleNamespace(status_code=200, headers={}, cookies=jar, json=lambda: {"ret": ["SUCCESS::ok"]})
        with patch.object(self.loaded.http, "post") as post:
            post.side_effect = lambda *args, **kwargs: expired if post.call_count == 1 else stale_refresh()
            with patch.object(refresh, "refresh_cookies_via_browser") as browser, \
                 patch.object(mtop, "generate_sign", return_value="synthetic-sign"):
                with self.assertRaises(SessionChangedError):
                    mtop.call(self.loaded, "synthetic.api", {})
            browser.assert_not_called()
            self.assertEqual(post.call_count, 2)
