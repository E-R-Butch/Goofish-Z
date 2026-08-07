"""MCP 入口 — 扫描 registry，每个命令自动注册为 MCP 工具。

Agent（Hermes/Claude Code 等）通过 MCP 直接调用全部命令。
"""
from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any

from goofish_omni.core.registry import discover, iter_commands


def main() -> None:
    """FastMCP 服务主入口。"""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("goofish-omni")

    discover()
    for cmd in iter_commands():
        sig = inspect.signature(cmd.func)

        # FastMCP 需要具体参数名生成 pydantic schema，**kwargs 会生成错误的 schema。
        # 方案：动态构造一个带显式参数的包装函数，其签名与原命令一致。
        func = cmd.func
        name = f"{cmd.namespace}.{cmd.name}"

        # 用 types.FunctionType 动态创建，显式复制原函数签名
        import types

        def _make_handler(cmd_func, cmd_sig):
            async def handler(*args, **kwargs):
                # 从 args/kwargs 组装，只传签名里存在的参数
                bound = cmd_sig.bind(*args, **kwargs)
                bound.apply_defaults()

                def _run():
                    result = cmd_func(**bound.arguments)
                    if inspect.iscoroutine(result):
                        return asyncio.run(result)
                    return result

                try:
                    # 同步命令（内部可能用 asyncio.run）放到线程执行，
                    # 避免"running event loop"冲突；async 命令直接 await。
                    result = await asyncio.to_thread(_run)
                    # FastMCP 会自动序列化 dict 返回值；字符串会被当 schema 校验失败
                    if isinstance(result, str):
                        try:
                            return json.loads(result)
                        except (TypeError, ValueError):
                            return {"result": result}
                    return result
                except Exception as e:
                    return {"error": str(e)}

            handler.__name__ = cmd_func.__name__
            handler.__doc__ = cmd_func.__doc__
            # 关键：让 FastMCP 用原函数签名生成 schema
            handler.__signature__ = cmd_sig  # type: ignore[attr-defined]
            return handler

        handler = _make_handler(func, sig)
        mcp.tool(name=name, description=cmd.description)(handler)

    mcp.run()


if __name__ == "__main__":
    main()
