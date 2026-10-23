"""Offline watch regressions: an obsolete session must stop, not retry forever."""

import asyncio
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import requests

from goofish_z.core import guard, mtop, refresh, session, token, ws
from goofish_z.core.errors import SessionChangedError


def cookies(account="synthetic-A", value="synthetic-old"):
    return [{"name": name, "value": value, "domain": ".goofish.com", "path": "/"}
            for name, value in {"unb": account, "_m_h5_tk": value + "_0",
                                "cookie2": value + "-session"}.items()]


def response(ret="SUCCESS::ok", value=None):
    jar = requests.cookies.RequestsCookieJar()
    if value is not None:
        jar.set("_m_h5_tk", value + "_0", domain=".goofish.com", path="/")
    return SimpleNamespace(status_code=200, headers={}, cookies=jar,
                           json=lambda: {"ret": [ret]})


class ConnectedWatch:
    """A connected receiver/heartbeat whose asynchronous cleanup is observable."""

    def __init__(self, *, frames=(), cleanup_delay=0):
        self.frames = list(frames)
        self.cleanup_delay = cleanup_delay
        self.accounts = []
        self.receiving = asyncio.Event()
        self.beating = asyncio.Event()
        self.receiver_closed = False
        self.heartbeat_closed = False
        self.connection_closed = False

    @asynccontextmanager
    async def connect(self, current):
        self.accounts.append(current.unb)
        try:
            yield self
        finally:
            await asyncio.sleep(self.cleanup_delay)
            self.connection_closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.receiving.set()
        if self.frames:
            return self.frames.pop(0)
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(self.cleanup_delay)
            self.receiver_closed = True

    async def heartbeat(self, _socket):
        self.beating.set()
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(self.cleanup_delay)
            self.heartbeat_closed = True

    async def send(self, _data):
        pass


class WatchSessionChangeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="goofish-watch-session-test-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.path = root / "cookies.json"
        for patcher in (
            patch.dict("os.environ", {"GOOFISH_Z_DATA": str(root),
                                      "GOOFISH_AUTO_REFRESH_TOKEN": "1",
                                      "GOOFISH_IM_TOKEN": ""}),
            patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden")),
            patch.object(ws.websockets, "connect", side_effect=AssertionError("network forbidden")),
            patch.object(session, "generate_device_id", return_value="synthetic-device"),
            patch.object(session, "generate_sign", return_value="synthetic-sign"),
            patch.object(mtop, "generate_sign", return_value="synthetic-sign"),
            patch.object(guard, "STATE_PATH", root / "circuit.json"),
            patch.object(token, "TOKEN_CACHE", root / "im_token.json"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.load_original()

    def load_original(self):
        session.write_cookies_json(self.path, cookies())
        self.loaded = session.Session.load(self.path, allow_bootstrap=False)

    def replace_session(self, account):
        if account == "synthetic-A":
            # Ordinary refresh by another command, not an explicit account switch.
            other = session.Session.load(self.path, allow_bootstrap=False)
            with patch.object(other.http, "post", return_value=response(value="synthetic-new")):
                self.assertTrue(other.refresh_token())
        else:
            session.write_cookies_json(self.path, cookies(account, "synthetic-new"))
        return self.path.read_bytes()

    def assert_no_watch_tasks(self, before):
        self.assertEqual({task for task in asyncio.all_tasks() - before if not task.done()}, set())

    def assert_closed(self, connected):
        self.assertTrue(connected.receiver_closed)
        self.assertTrue(connected.heartbeat_closed)
        self.assertTrue(connected.connection_closed)
        self.assertEqual(connected.accounts, ["synthetic-A"])

    async def test_keepalive_surfaces_rotation_and_account_change_and_closes_connection(self):
        for account in ("synthetic-A", "synthetic-B"):
            with self.subTest(account=account):
                self.load_original()
                connected = ConnectedWatch()
                replaced = asyncio.Event()
                real_sleep = asyncio.sleep

                async def sleep(delay):
                    if delay == 3600:
                        await replaced.wait()
                    else:
                        await real_sleep(delay)

                before = asyncio.all_tasks()
                with (
                    patch.object(ws, "get_access_token", return_value="synthetic-im-token"),
                    patch.object(ws, "connect", side_effect=connected.connect),
                    patch.object(ws, "register", new=AsyncMock()),
                    patch.object(ws, "heartbeat_loop", side_effect=connected.heartbeat),
                    patch.object(self.loaded.http, "post", return_value=response("FAIL_SYS_TOKEN_EXOIRED::expired")) as post,
                    patch.object(refresh, "refresh_cookies_via_browser") as browser,
                    patch.object(ws.asyncio, "sleep", side_effect=sleep),
                ):
                    watching = asyncio.create_task(ws.run_forever(self.loaded, refresh_every=3600))
                    try:
                        await asyncio.wait_for(connected.receiving.wait(), 1)
                        await asyncio.wait_for(connected.beating.wait(), 1)
                        newer = self.replace_session(account)
                        replaced.set()
                        with self.assertRaises(SessionChangedError):
                            await asyncio.wait_for(watching, 1)
                    finally:
                        watching.cancel()
                        await asyncio.gather(watching, return_exceptions=True)
                self.assertEqual(post.call_count, 1)
                self.assertIn("loginuser.get", post.call_args.args[0])
                browser.assert_not_called()
                self.assertEqual(self.path.read_bytes(), newer)
                self.assertEqual(self.loaded.unb, "synthetic-A")
                self.assertEqual(self.loaded.http.cookies.get("_m_h5_tk"), "synthetic-old_0")
                self.assert_closed(connected)
                self.assert_no_watch_tasks(before)

    async def test_reconnect_surfaces_rotation_and_account_change_without_retry(self):
        for account in ("synthetic-A", "synthetic-B"):
            with self.subTest(account=account):
                self.load_original()
                newer = self.replace_session(account)
                before = asyncio.all_tasks()
                with (
                    patch.object(self.loaded.http, "post", return_value=response("FAIL_SYS_TOKEN_EXOIRED::expired")) as post,
                    patch.object(ws, "connect") as connect,
                    patch.object(token, "refresh_login") as keepalive,
                    patch.object(refresh, "refresh_cookies_via_browser") as browser,
                ):
                    with self.assertRaises(SessionChangedError):
                        await asyncio.wait_for(ws.run_forever(self.loaded, refresh_every=3600), 0.5)
                self.assertEqual(post.call_count, 1)
                self.assertIn("login.token", post.call_args.args[0])
                connect.assert_not_called()
                keepalive.assert_not_called()
                browser.assert_not_called()
                self.assertEqual(self.path.read_bytes(), newer)
                self.assertEqual(self.loaded.unb, "synthetic-A")
                self.assertEqual(self.loaded.http.cookies.get("_m_h5_tk"), "synthetic-old_0")
                self.assert_no_watch_tasks(before)

    async def test_keepalive_failure_interrupts_reconnect_backoff(self):
        changed = SessionChangedError("synthetic session replaced")
        before = asyncio.all_tasks()
        with (
            patch.object(ws, "get_access_token", side_effect=OSError("synthetic disconnected")) as get_token,
            patch.object(token, "refresh_login", side_effect=changed),
        ):
            with self.assertRaises(SessionChangedError) as caught:
                await asyncio.wait_for(ws.run_forever(self.loaded, refresh_every=0.01), 0.5)
        self.assertIs(caught.exception, changed)
        self.assertEqual(get_token.call_count, 1)
        self.assert_no_watch_tasks(before)

    async def test_transient_reconnect_errors_still_use_exponential_backoff(self):
        changed = SessionChangedError("synthetic session replaced")
        delays = []
        real_sleep = asyncio.sleep

        async def sleep(delay):
            if delay == 3600:
                await asyncio.Future()
            delays.append(delay)
            await real_sleep(0)

        before = asyncio.all_tasks()
        with (
            patch.object(ws, "get_access_token", side_effect=[OSError("synthetic 1"), OSError("synthetic 2"), changed]) as get_token,
            patch.object(ws.asyncio, "sleep", side_effect=sleep),
        ):
            with self.assertRaises(SessionChangedError) as caught:
                await asyncio.wait_for(ws.run_forever(self.loaded, refresh_every=3600), 1)
        self.assertIs(caught.exception, changed)
        self.assertEqual(get_token.call_count, 3)
        self.assertEqual(delays, [1.0, 2.0])
        self.assert_no_watch_tasks(before)

    async def test_transient_keepalive_errors_still_retry(self):
        changed = SessionChangedError("synthetic session replaced")
        connected = ConnectedWatch()
        before = asyncio.all_tasks()
        with (
            patch.object(ws, "get_access_token", return_value="synthetic-im-token"),
            patch.object(ws, "connect", side_effect=connected.connect),
            patch.object(ws, "register", new=AsyncMock()),
            patch.object(ws, "heartbeat_loop", side_effect=connected.heartbeat),
            patch.object(token, "refresh_login", side_effect=[OSError("synthetic transient"), changed]) as keepalive,
        ):
            with self.assertRaises(SessionChangedError) as caught:
                await asyncio.wait_for(ws.run_forever(self.loaded, refresh_every=0.01), 1)
        self.assertIs(caught.exception, changed)
        self.assertEqual(keepalive.call_count, 2)
        self.assert_closed(connected)
        self.assert_no_watch_tasks(before)

    async def test_caller_cancellation_waits_for_all_connection_cleanup(self):
        connected = ConnectedWatch(cleanup_delay=0.01)
        before = asyncio.all_tasks()
        with (
            patch.object(ws, "get_access_token", return_value="synthetic-im-token"),
            patch.object(ws, "connect", side_effect=connected.connect),
            patch.object(ws, "register", new=AsyncMock()),
            patch.object(ws, "heartbeat_loop", side_effect=connected.heartbeat),
            patch.object(token, "refresh_login") as keepalive,
        ):
            watching = asyncio.create_task(ws.run_forever(self.loaded, refresh_every=3600))
            try:
                await asyncio.wait_for(connected.receiving.wait(), 1)
                await asyncio.wait_for(connected.beating.wait(), 1)
            finally:
                watching.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await watching
        keepalive.assert_not_called()
        self.assert_closed(connected)
        self.assert_no_watch_tasks(before)

    async def test_watch_can_refresh_its_own_session_repeatedly(self):
        connected = ConnectedWatch()
        refreshed = asyncio.Event()
        replies = [response("FAIL_SYS_TOKEN_EXOIRED::expired"),
                   response(value="synthetic-first"), response(),
                   response("FAIL_SYS_TOKEN_EXOIRED::expired"),
                   response(value="synthetic-second"), response()]

        def post(*args, **kwargs):
            result = replies.pop(0)
            if not replies:
                refreshed.set()
            return result

        before = asyncio.all_tasks()
        with (
            patch.object(ws, "get_access_token", return_value="synthetic-im-token"),
            patch.object(ws, "connect", side_effect=connected.connect),
            patch.object(ws, "register", new=AsyncMock()),
            patch.object(ws, "heartbeat_loop", side_effect=connected.heartbeat),
            patch.object(self.loaded.http, "post", side_effect=post) as posted,
            patch.object(refresh, "refresh_cookies_via_browser") as browser,
        ):
            watching = asyncio.create_task(ws.run_forever(self.loaded, refresh_every=0.01))
            try:
                await asyncio.wait_for(refreshed.wait(), 1)
                self.assertFalse(watching.done())
            finally:
                watching.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await watching
        self.assertEqual(posted.call_count, 6)
        browser.assert_not_called()
        self.assertEqual(self.loaded.http.cookies.get("_m_h5_tk"), "synthetic-second_0")
        self.assertEqual(session._load_cookies(self.path)["_m_h5_tk"], "synthetic-second_0")
        self.assert_closed(connected)
        self.assert_no_watch_tasks(before)

    async def test_handler_session_change_is_not_swallowed(self):
        connected = ConnectedWatch(frames=['{"headers": {}}'])
        changed = SessionChangedError("synthetic session replaced")

        async def handler(_msg, _socket):
            await connected.beating.wait()
            raise changed

        before = asyncio.all_tasks()
        with (
            patch.object(ws, "get_access_token", return_value="synthetic-im-token") as get_token,
            patch.object(ws, "connect", side_effect=connected.connect),
            patch.object(ws, "register", new=AsyncMock()),
            patch.object(ws, "heartbeat_loop", side_effect=connected.heartbeat),
        ):
            with self.assertRaises(SessionChangedError) as caught:
                await asyncio.wait_for(ws.run_forever(self.loaded, handler, refresh_every=3600), 1)
        self.assertIs(caught.exception, changed)
        self.assertEqual(get_token.call_count, 1)
        self.assertTrue(connected.heartbeat_closed)
        self.assertTrue(connected.connection_closed)
        self.assertEqual(connected.accounts, ["synthetic-A"])
        self.assert_no_watch_tasks(before)
