import asyncio
from unittest.mock import Mock, patch

import requests

from support import OfflineCase
from goofish_z.commands.watch.jobs import watch_cancel, watch_job, watch_jobs, watch_start
from goofish_z.core.errors import GoofishError
from goofish_z.mcp_server import make_handler


class AgentJobsTest(OfflineCase):
    def test_agent_starts_and_queries_the_shared_http_job(self):
        response = Mock(ok=True)
        response.json.return_value = {"id": "synthetic-job", "status": "queued", "result": None}
        with patch("goofish_z.commands.watch.jobs.requests.request", return_value=response) as request:
            self.assertEqual(watch_start(all=True)["id"], "synthetic-job")
            self.assertEqual(request.call_args.args[:2], ("POST", "http://127.0.0.1:8787/api/watch/jobs"))
            self.assertTrue(request.call_args.kwargs["json"]["all"])
            watch_job("synthetic-job")
            self.assertEqual(request.call_args.args[0], "GET")
            watch_cancel("synthetic-job")
            self.assertEqual(request.call_args.args[0], "DELETE")

    def test_invalid_target_is_rejected_without_http_or_marketplace_calls(self):
        with patch("goofish_z.commands.watch.jobs.requests.request") as request:
            with self.assertRaises(ValueError):
                watch_start()
            request.assert_not_called()

    def test_connection_error_explains_how_to_start_the_service(self):
        with patch("goofish_z.commands.watch.jobs.requests.request", side_effect=requests.ConnectionError):
            with self.assertRaisesRegex(GoofishError, "HTTP API"):
                watch_jobs()

    def test_failed_job_remains_structured_for_agent_inspection(self):
        result = {"id": "synthetic-job", "status": "failed", "result": {
            "status": "failed", "results": [{"error": "synthetic failure"}]}}
        response = Mock(ok=True)
        response.json.return_value = result
        with patch("goofish_z.commands.watch.jobs.requests.request", return_value=response):
            self.assertEqual(asyncio.run(make_handler(watch_job)(job_id="synthetic-job")), result)
