"""FastAPI 层 — 把 registry 命令自动暴露为 HTTP API + 挂载 GUI 静态页。

设计：命令层是唯一业务逻辑来源；API 层只是薄封装。
每个命令 → GET/POST /api/<namespace>/<name>。
"""
from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from goofish_z.core.registry import discover, iter_commands
from goofish_z import __version__
from goofish_z.core.errors import AuthRequiredError, GoofishError, NotFoundError, RateLimitedError, RiskControlError
from goofish_z.api.models import RuleAdd, RuleRemove, SellerUnban, WatchAdd, WatchRun
from goofish_z.api.jobs import JobBusyError, WatchJobs

@asynccontextmanager
async def lifespan(app):
    app.state.watch_jobs = WatchJobs()
    try:
        yield
    finally:
        app.state.watch_jobs.close()


app = FastAPI(title="Goofish-Z", version=__version__, lifespan=lifespan)

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

    try:
        bound = inspect.signature(cmd.func).bind(**params)
    except TypeError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        return cmd.func(*bound.args, **bound.kwargs)
    except RateLimitedError as e:
        retry_after = max(1, int((e.retry_after or 1) + 0.999))
        raise HTTPException(
            429,
            "请求过于频繁，请稍后重试",
            headers={"Retry-After": str(retry_after)},
        )
    except AuthRequiredError as e:
        raise HTTPException(401, str(e)) from e
    except RiskControlError as e:
        raise HTTPException(503, str(e)) from e
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except GoofishError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception(f"命令 {full_name} 失败")
        raise HTTPException(500, "命令执行失败，请查看服务日志") from e


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


@app.get("/api/item/mine")
def api_item_mine(
    status: str = Query("online", description="online/sold/offline/all"),
    limit: int = Query(20, ge=1, le=200),
) -> JSONResponse:
    """列出当前账号发布的商品，默认只返回在售。"""
    return JSONResponse(_call_command("item.mine", {"status": status, "limit": limit}))


@app.get("/api/item/get")
def api_item_get(
    item_id: str = Query(..., min_length=1, pattern=r"^\d+$", description="闲鱼商品 ID"),
) -> JSONResponse:
    """按商品 ID 读取详情；供 localhost 消费者按需补全字段。"""
    result = _call_command("item.get", {"item_id": item_id})
    if not isinstance(result, dict):
        raise HTTPException(500, "item.get 返回结构非预期")
    public_fields = ("item_id", "title", "price", "status", "detail")
    return JSONResponse({field: result[field] for field in public_fields if field in result})


@app.get("/api/watch")
def api_watch_list() -> JSONResponse:
    return JSONResponse(_call_command("watch.list", {}))


@app.post("/api/watch")
def api_watch_add(body: WatchAdd) -> JSONResponse:
    return JSONResponse(_call_command("watch.add", body.model_dump()))


@app.delete("/api/watch/{watch_id}")
def api_watch_remove(watch_id: int) -> JSONResponse:
    return JSONResponse(_call_command("watch.remove", {"watch_id": watch_id}))


@app.get("/api/watch/{watch_id}/history")
def api_watch_history(watch_id: int, limit: int = 50) -> JSONResponse:
    return JSONResponse(_call_command("watch.history", {"watch_id": watch_id, "limit": limit}))


@app.post("/api/watch/run")
def api_watch_run(body: WatchRun) -> JSONResponse:
    return _watch_response(_call_command("watch.run", body.model_dump()))


def _watch_response(result: dict) -> JSONResponse:
    code = 502 if result.get("status") == "failed" else 207 if result.get("status") == "partial" else 200
    return JSONResponse(result, status_code=code)


@app.post("/api/watch/jobs", status_code=202)
def api_watch_job_start(body: WatchRun):
    try:
        return app.state.watch_jobs.start(body.model_dump())
    except JobBusyError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/watch/jobs")
def api_watch_jobs():
    return {"jobs": app.state.watch_jobs.recent()}


@app.get("/api/watch/jobs/{job_id}")
def api_watch_job_get(job_id: str):
    try:
        return app.state.watch_jobs.get(job_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在，服务重启后需重新发起") from exc


@app.delete("/api/watch/jobs/{job_id}")
def api_watch_job_cancel(job_id: str):
    try:
        return app.state.watch_jobs.cancel(job_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc


@app.get("/api/alerts")
def api_alerts(limit: int = Query(50, ge=1, le=200), unread_only: bool = False):
    return _call_command("watch.alerts", {"limit": limit, "unread_only": unread_only})


@app.post("/api/alerts/{alert_id}/read")
def api_alert_read(alert_id: int):
    return _call_command("watch.read-alert", {"alert_id": alert_id})


# ---- 黑名单（App 依赖）----
@app.get("/api/blacklist")
def api_blacklist_list() -> JSONResponse:
    return JSONResponse(_call_command("blacklist.list", {}))


@app.post("/api/blacklist/add")
def api_blacklist_add(body: RuleAdd) -> JSONResponse:
    return JSONResponse(_call_command("blacklist.add", body.model_dump()))


@app.post("/api/blacklist/remove")
def api_blacklist_remove(body: RuleRemove) -> JSONResponse:
    return JSONResponse(_call_command("blacklist.remove", body.model_dump()))


# ---- 信号引擎（App 依赖）----
@app.get("/api/signals/list")
def api_signals_list(only_banned: bool = False) -> JSONResponse:
    return JSONResponse(_call_command("signals.list", {"only_banned": only_banned}))


@app.post("/api/signals/unban")
def api_signals_unban(body: SellerUnban) -> JSONResponse:
    return JSONResponse(_call_command("signals.unban", body.model_dump()))


@app.post("/api/watch/run-all")
def api_watch_run_all() -> JSONResponse:
    return _watch_response(_call_command("watch.run", {"all": True}))


@app.get("/api/message/chats")
def api_message_chats() -> JSONResponse:
    return JSONResponse(_call_command("message.list-chats", {}))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/diagnostics")
def api_diagnostics():
    return _call_command("auth.doctor", {})


# GUI 静态页挂载
_gui_dir = __import__("pathlib").Path(__file__).parent.parent / "gui"
if _gui_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_gui_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    idx = _gui_dir / "index.html"
    if idx.exists():
        return idx.read_text(encoding="utf-8")
    return "<h1>Goofish-Z</h1><p>GUI 未构建</p>"


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8787, log_level="info")


if __name__ == "__main__":
    main()
