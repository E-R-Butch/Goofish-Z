import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch

from mcp import Client, StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError

from support import OfflineCase, fixture
from goofish_z.core.errors import RateLimitedError
from goofish_z.mcp_server import create_server, make_handler


class McpTest(OfflineCase):
    def test_sync_command_can_run_its_own_async_browser_loop(self):
        async def fetched(*args):
            return {"items": [fixture()], "page": 1, "has_next": False, "source_count": 1}
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
        self.assertIn("query", by_name["search.items"].input_schema["properties"])
        self.assertIn("all", by_name["watch.run"].input_schema["properties"])
        self.assertIn("auth.doctor",by_name)
        for tool in tools:
            with self.subTest(tool=tool.name):
                self.assertIsNotNone(tool.output_schema)

    def test_failed_monitor_result_is_reported_as_tool_error(self):
        def failed():
            return {"status": "failed", "results": [{"error": "synthetic failure"}]}
        with self.assertRaisesRegex(ToolError, "synthetic failure"):
            asyncio.run(make_handler(failed)())

    def test_stdio_supports_both_protocol_eras_and_isolates_prints(self):
        source = '''
import subprocess
import sys
from goofish_z.core.errors import GoofishError
from goofish_z.mcp_server import create_server, make_handler

def noisy() -> dict[str, bool]:
    print("synthetic handler output")
    subprocess.run([sys.executable, "-c", "print('synthetic child output')"], check=True)
    return {"ok": True}

def failing() -> dict[str, bool]:
    raise GoofishError("synthetic failure")

server = create_server()
server.tool(name="synthetic.noisy")(make_handler(noisy))
server.tool(name="synthetic.failing")(make_handler(failing))
server.run()
'''
        env = {**os.environ, "GOOFISH_Z_DATA": str(self.root),
               "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        params = StdioServerParameters(command=sys.executable, args=["-c", source], env=env)

        async def call(mode):
            async with Client(params, mode=mode, read_timeout_seconds=10) as client:
                tools = await client.list_tools()
                self.assertIn("auth.doctor", {tool.name for tool in tools.tools})
                result = await client.call_tool("synthetic.noisy")
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content, {"ok": True})
                error = await client.call_tool("synthetic.failing")
                self.assertTrue(error.is_error)
                self.assertIn("synthetic failure", error.content[0].text)

        for mode in ("legacy", "2026-07-28"):
            with self.subTest(mode=mode), self.assertNoLogs("mcp.client.stdio", level="ERROR"):
                asyncio.run(call(mode))
