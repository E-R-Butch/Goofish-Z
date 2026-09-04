import importlib
import time
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from support import OfflineCase, fixture
from goofish_z.core.errors import GoofishError
from goofish_z.db import WatchDB

api = importlib.import_module("goofish_z.api.app")
jobs = importlib.import_module("goofish_z.api.jobs")


class ApiReliabilityTest(OfflineCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(api.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.db = WatchDB(self.watch.DEFAULT_DB)
        self.wid = self.db.add_watch("synthetic", max_price=100)

    def test_actual_http_route_can_run_async_browser_command(self):
        async def fetched(*args):
            return [fixture()]
        with patch.object(self.search, "_run", side_effect=fetched):
            response = self.client.post("/api/watch/run", json={"all": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["succeeded"], 1)

    def test_total_failure_is_not_http_success(self):
        with patch.object(self.search, "search", side_effect=GoofishError("synthetic failure")):
            response = self.client.post("/api/watch/run-all")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["status"], "failed")

    def test_invalid_monitor_requests_are_rejected_before_execution(self):
        for body in ({}, {"all": True, "watch_id": 1}, {"all": True, "limit": 100}, {"all": True, "typo": 1}):
            with self.subTest(body=body), patch.object(api, "_call_command") as command:
                response = self.client.post("/api/watch/run", json=body)
                self.assertEqual(response.status_code, 422)
                command.assert_not_called()

    def test_watch_prices_and_keyword_are_validated(self):
        for body in ({"keyword": "   "}, {"keyword": "synthetic", "max_price": -1}, {"keyword": "synthetic", "max_price": "bad"}):
            self.assertEqual(self.client.post("/api/watch",json=body).status_code,422)

    def test_chat_route_uses_registered_command_name(self):
        command = SimpleNamespace(func=lambda: {"chats": []})
        with patch.object(api, "_all_commands", return_value={"message.list-chats": command}):
            response = self.client.get("/api/message/chats")
        self.assertEqual(response.status_code, 200)

    def test_alerts_are_read_from_database_and_can_be_acknowledged(self):
        alert = self.db.record_poll(self.wid,[fixture()])[0]
        self.assertEqual(len(self.client.get("/api/alerts?unread_only=true").json()["alerts"]), 1)
        self.assertEqual(self.client.post(f"/api/alerts/{alert['id']}/read").status_code, 200)
        self.assertEqual(self.client.get("/api/alerts?unread_only=true").json()["alerts"], [])

    def test_background_job_remains_responsive_and_cancellable(self):
        started = Event()
        def worker(*, progress, cancel, **params):
            progress({"phase": "waiting", "completed": 0, "total": 1, "retry_after": 30})
            started.set()
            cancel.wait(2)
            return {"status": "cancelled", "results": []}
        with patch.object(jobs, "run_watches", side_effect=worker):
            response = self.client.post("/api/watch/jobs",json={"all":True})
            self.assertEqual(response.status_code,202)
            job_id = response.json()["id"]
            self.assertTrue(started.wait(1))
            self.assertEqual(self.client.get("/health").status_code,200)
            self.assertEqual(self.client.post("/api/watch/jobs",json={"all":True}).status_code,409)
            self.assertEqual(self.client.delete(f"/api/watch/jobs/{job_id}").status_code,200)
            deadline = time.monotonic()+2
            while time.monotonic() < deadline:
                final = self.client.get(f"/api/watch/jobs/{job_id}").json()
                if final["result"] is not None:
                    break
                time.sleep(0.01)
            self.assertEqual(final["status"],"cancelled")

    def test_diagnostics_does_not_access_credentials_or_network(self):
        response = self.client.get("/api/diagnostics")
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()["auth"]["cookies_present"])
        self.assertIsNone(response.json()["auth"]["last_check"])
        self.assertFalse((self.root/"limiter.json").exists())

    def test_agent_and_web_share_the_same_background_job_and_alerts(self):
        from goofish_z.commands.watch.jobs import watch_job, watch_start

        def local_request(method, url, *, json, timeout):
            response = self.client.request(method, url, json=json)
            return SimpleNamespace(ok=response.is_success, status_code=response.status_code, json=response.json)

        with patch("goofish_z.commands.watch.jobs.requests.request", side_effect=local_request), \
             patch.object(self.search, "search", return_value={"items": [fixture()]}):
            submitted = watch_start(all=True)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                result = watch_job(submitted["id"])
                if result["result"] is not None:
                    break
                time.sleep(0.01)
            self.assertEqual(result["result"]["succeeded"], 1)
            web = self.client.get(f"/api/watch/jobs/{submitted['id']}").json()
            self.assertEqual(result, web)
            self.assertEqual(len(self.client.get("/api/alerts").json()["alerts"]), 1)
