import asyncio
from unittest.mock import patch

from mcp.server.fastmcp.exceptions import ToolError

from support import OfflineCase, fixture
from goofish_z.core.errors import RateLimitedError
from goofish_z.mcp_server import create_server, make_handler


class McpTest(OfflineCase):
    def test_sync_command_can_run_its_own_async_browser_loop(self):
        async def fetched(*args):
            return [fixture()]
        with patch.object(self.search, "_run", side_effect=fetched):
            result = asyncio.run(make_handler(self.search.search)(query="synthetic"))
        self.assertEqual(result["count"],1)

    def test_errors_are_mcp_tool_errors(self):
        def limited():
            raise RateLimitedError("synthetic wait",retry_after=30)
        with self.assertRaises(ToolError):
            asyncio.run(make_handler(limited)())

    def test_all_registered_tools_have_concrete_input_schemas(self):
        tools = asyncio.run(create_server().list_tools())
        by_name = {tool.name:tool for tool in tools}
        self.assertIn("query", by_name["search.items"].inputSchema["properties"])
        self.assertIn("all", by_name["watch.run"].inputSchema["properties"])
        self.assertIn("auth.doctor",by_name)

    def test_failed_monitor_result_is_reported_as_tool_error(self):
        def failed():
            return {"status": "failed", "results": [{"error": "synthetic failure"}]}
        with self.assertRaisesRegex(ToolError, "synthetic failure"):
            asyncio.run(make_handler(failed)())
