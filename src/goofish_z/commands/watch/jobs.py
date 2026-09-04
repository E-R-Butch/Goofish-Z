"""CLI/MCP access to the same background jobs displayed by the local Web UI."""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import requests

from goofish_z.api.models import WatchRun
from goofish_z.core.errors import GoofishError, NotFoundError
from goofish_z.core.registry import command


def _request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    base = os.environ.get("GOOFISH_Z_HTTP", "http://127.0.0.1:8787").rstrip("/")
    try:
        response = requests.request(method, base + path, json=body, timeout=(3, 10))
    except requests.RequestException as exc:
        raise GoofishError("无法连接本地监控服务，请先启动 Goofish-Z HTTP API 或检查 GOOFISH_Z_HTTP") from exc
    try:
        result = response.json()
    except ValueError as exc:
        raise GoofishError("监控服务返回非 JSON 响应，请检查服务地址和版本") from exc
    if not response.ok:
        detail = result.get("detail", "请求失败") if isinstance(result, dict) else "请求失败"
        if isinstance(detail, list):
            detail = "；".join(str(item.get("msg", "参数错误")) for item in detail)
        error = NotFoundError if response.status_code == 404 else GoofishError
        raise error(f"监控服务 HTTP {response.status_code}: {detail}")
    if not isinstance(result, dict):
        raise GoofishError("监控服务响应格式错误")
    return result


def _job_path(job_id: str) -> str:
    if not job_id.strip():
        raise ValueError("job_id 不能为空")
    return "/api/watch/jobs/" + quote(job_id.strip(), safe="")


@command(namespace="watch", name="start", description="提交后台监控并立即返回任务 ID；需本地 HTTP 服务，之后用 watch.job 查询")
def watch_start(watch_id: int | None = None, all: bool = False, limit: int = 20,
                enrich_sellers: bool = False) -> dict[str, Any]:
    request = WatchRun(watch_id=watch_id, all=all, limit=limit, enrich_sellers=enrich_sellers)
    return _request("POST", "/api/watch/jobs", request.model_dump())


@command(namespace="watch", name="job", description="查询后台监控进度和每项结果；不会重复发起搜索")
def watch_job(job_id: str) -> dict[str, Any]:
    return _request("GET", _job_path(job_id))


@command(namespace="watch", name="jobs", description="列出本地监控服务的最近任务，与网页共享进度")
def watch_jobs() -> dict[str, Any]:
    return _request("GET", "/api/watch/jobs")


@command(namespace="watch", name="cancel", description="请求取消后台监控；正在进行的网络请求结束后停止")
def watch_cancel(job_id: str) -> dict[str, Any]:
    return _request("DELETE", _job_path(job_id))
