"""FastAPI 层 — 把 registry 命令自动暴露为 HTTP API + 挂载 GUI 静态页。

设计：命令层是唯一业务逻辑来源；API 层只是薄封装。
每个命令 → GET/POST /api/<namespace>/<name>。
"""
from __future__ import annotations

import inspect
import json
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from goofish_z.core.registry import discover, iter_commands
from goofish_z.core.errors import GoofishError

app = FastAPI(title="goofish-omni", version="0.1.0")

_COMMANDS = None


def _all_commands() -> dict[str, Any]:
    global _COMMANDS
    if _COMMANDS is None:
        discover()
        _COMMANDS = {c.full_name: c for c in iter_commands()}
    return _COMMANDS


def _call_command(full_name: str, params: dict[str, Any]) -> Any:
    cmd = _all_commands().get(full_name)
    if not cmd:
        raise HTTPException(404, f"未知命令: {full_name}")

    # 只传函数签名里存在的参数
    sig = inspect.signature(cmd.func)
    valid = {}
    for name, val in params.items():
        if val is None:
            continue
        if name in sig.parameters:
            # 类型转换：float 参数
            p = sig.parameters[name]
            if p.annotation is float and not isinstance(val, (int, float)):
                try:
                    val = float(val)
                except (TypeError, ValueError):
                    raise HTTPException(400, f"参数 {name} 需要数字")
            valid[name] = val
    try:
        return cmd.func(**valid)
    except GoofishError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception(f"命令 {full_name} 失败")
        raise HTTPException(500, str(e))


@app.get("/api/commands")
def list_api_commands() -> dict[str, Any]:
    return {
        "commands": [
            {"name": c.full_name, "description": c.description, "write": c.write}
            for c in _all_commands().values()
        ]
    }


@app.get("/api/search")
def api_search(
    q: str = Query(..., description="搜索关键词"),
    limit: int = Query(20, ge=1, le=50),
) -> JSONResponse:
    """搜索闲鱼商品。"""
    result = _call_command("search.items", {"query": q, "limit": limit})
    return JSONResponse(result)


@app.get("/api/watch")
def api_watch_list() -> JSONResponse:
    return JSONResponse(_call_command("watch.list", {}))


@app.post("/api/watch")
async def api_watch_add(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(_call_command("watch.add", body))


@app.delete("/api/watch/{watch_id}")
def api_watch_remove(watch_id: int) -> JSONResponse:
    return JSONResponse(_call_command("watch.remove", {"watch_id": watch_id}))


@app.get("/api/watch/{watch_id}/history")
def api_watch_history(watch_id: int, limit: int = 50) -> JSONResponse:
    return JSONResponse(_call_command("watch.history", {"watch_id": watch_id, "limit": limit}))


@app.post("/api/watch/run")
async def api_watch_run(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(_call_command("watch.run", body))


# ---- 黑名单（App 依赖）----
@app.get("/api/blacklist")
def api_blacklist_list() -> JSONResponse:
    return JSONResponse(_call_command("blacklist.list", {}))


@app.post("/api/blacklist/add")
async def api_blacklist_add(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(_call_command("blacklist.add", body))


@app.post("/api/blacklist/remove")
async def api_blacklist_remove(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(_call_command("blacklist.remove", body))


# ---- 信号引擎（App 依赖）----
@app.get("/api/signals/list")
def api_signals_list(only_banned: bool = False) -> JSONResponse:
    return JSONResponse(_call_command("signals.list", {"only_banned": only_banned}))


@app.post("/api/signals/unban")
async def api_signals_unban(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(_call_command("signals.unban", body))


@app.post("/api/watch/run-all")
async def api_watch_run_all() -> JSONResponse:
    return JSONResponse(_call_command("watch.run", {"all": True}))


@app.get("/api/message/chats")
def api_message_chats() -> JSONResponse:
    return JSONResponse(_call_command("message.list_chats", {}))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# GUI 静态页挂载
_gui_dir = __import__("pathlib").Path(__file__).parent.parent / "gui"
if _gui_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_gui_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    idx = _gui_dir / "index.html"
    if idx.exists():
        return idx.read_text(encoding="utf-8")
    return "<h1>goofish-omni</h1><p>GUI 未构建</p>"


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8787, log_level="info")


if __name__ == "__main__":
    main()
