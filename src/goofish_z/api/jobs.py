"""One bounded background monitor queue per API process."""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event, Lock
from uuid import uuid4

from loguru import logger

from goofish_z.commands.watch.watch import run_watches


class JobBusyError(Exception):
    pass


class WatchJobs:
    def __init__(self):
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="goofish-watch")
        self._jobs = OrderedDict()
        self._cancel = {}

    def start(self, params: dict) -> dict:
        with self._lock:
            if any(j["status"] in ("queued", "running") for j in self._jobs.values()):
                raise JobBusyError("已有监控任务运行，请查看进度或等待完成")
            job_id = uuid4().hex
            job = {"id": job_id, "status": "queued", "progress": {}, "result": None}
            self._jobs[job_id] = job
            self._cancel[job_id] = Event()
            while len(self._jobs) > 50:
                old, _ = self._jobs.popitem(last=False)
                self._cancel.pop(old, None)
            self._executor.submit(self._run, job_id, dict(params))
            return deepcopy(job)

    def _run(self, job_id: str, params: dict):
        with self._lock:
            self._jobs[job_id]["status"] = "running"
            cancel = self._cancel[job_id]

        def progress(value):
            with self._lock:
                self._jobs[job_id]["progress"] = value

        try:
            result = run_watches(**params, progress=progress, cancel=cancel)
        except Exception:
            logger.exception("后台监控失败")
            result = {"status": "failed", "results": [], "error": "监控任务失败，请查看服务日志"}
        with self._lock:
            self._jobs[job_id].update(status=result["status"], result=result)

    def get(self, job_id: str) -> dict:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            return deepcopy(self._jobs[job_id])

    def recent(self) -> list[dict]:
        with self._lock:
            return deepcopy(list(reversed(self._jobs.values()))[:10])

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            self._cancel[job_id].set()
            self._jobs[job_id]["cancel_requested"] = True
            return deepcopy(self._jobs[job_id])

    def close(self):
        with self._lock:
            for event in self._cancel.values():
                event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
