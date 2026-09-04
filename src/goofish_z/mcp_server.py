"""Expose the shared commands as MCP tools with consistent errors."""
from __future__ import annotations

import asyncio
import inspect
import json

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from loguru import logger

from goofish_z.core.errors import GoofishError
from goofish_z.core.registry import discover, iter_commands


def make_handler(func):
    signature = inspect.signature(func, eval_str=True)

    async def handler(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()

        def run():
            result = func(*bound.args, **bound.kwargs)
            if inspect.isawaitable(result):
                result = asyncio.run(result)
            return result

        try:
            result = await asyncio.to_thread(run)
            if isinstance(result, dict) and result.get("status") == "failed":
                errors = [r.get("error", "") for r in result.get("results", []) if r.get("error")]
                raise ToolError("监控失败：" + ("；".join(errors) or result.get("error", "请查看服务日志")))
            if isinstance(result, str):
                try:
                    return json.loads(result)
                except ValueError:
                    return {"result": result}
            return result
        except ToolError:
            raise
        except (GoofishError, ValueError) as exc:
            raise ToolError(f"[{type(exc).__name__}] {exc}") from exc
        except Exception as exc:
            logger.exception("MCP command failed")
            raise ToolError("命令执行失败，请查看服务日志") from exc

    handler.__name__ = func.__name__
    handler.__doc__ = func.__doc__
    handler.__signature__ = signature
    return handler


def create_server() -> FastMCP:
    server = FastMCP("Goofish-Z")
    discover()
    for cmd in iter_commands():
        server.tool(name=cmd.full_name, description=cmd.description)(make_handler(cmd.func))
    return server


def main() -> None:
    create_server().run()


if __name__ == "__main__":
    main()
