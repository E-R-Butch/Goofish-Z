"""分 bucket 限流 — 不同操作不同频率上限。

参考 ai-goofish-monitor 实战参数 + 上游 goofish-cli 令牌桶：
- search      : 30s 一次（搜索列表，轻）
- detail      : 5s 一次（商品详情，中）
- write       : 60s 一次（发布/发消息，重）
- seller_page : 4s 一次（卖家昵称页面抓取）

状态文件：~/.goofish-z/limiter.json（进程间共享）
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from goofish_z.core.errors import RateLimitedError

DATA_DIR = Path(os.environ.get("GOOFISH_Z_DATA", str(Path.home() / ".goofish-z")))
STATE_PATH = DATA_DIR / "limiter.json"

# bucket → (窗口秒数, 窗口内上限)。默认即安全值。
BUCKETS: dict[str, tuple[int, int]] = {
    "search": (30, 1),        # 30s 1 次搜索
    "detail": (5, 1),         # 5s 1 次详情
    "seller_page": (4, 1),    # 4s 1 次页面抓取
    "write": (60, 1),         # 60s 1 次写操作
}


def _bucket_conf(bucket: str) -> tuple[int, int]:
    window, limit = BUCKETS.get(bucket, (10, 1))
    # 环境变量可覆盖：GOOFISH_LIMIT_<BUCKET>_SEC / _RPM
    try:
        window = int(os.environ.get(f"GOOFISH_LIMIT_{bucket.upper()}_SEC", window))
    except ValueError:
        pass
    try:
        limit = int(os.environ.get(f"GOOFISH_LIMIT_{bucket.upper()}_RPM", limit))
    except ValueError:
        pass
    return max(1, window), max(1, limit)


def _load() -> dict[str, list[float]]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(state: dict[str, list[float]]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


def check(bucket: str) -> float:
    """消耗一个令牌。超限返回还需等待的秒数（>0 = 需要等）。"""
    now = time.time()
    window, limit = _bucket_conf(bucket)
    state = _load()
    hits = [t for t in state.get(bucket, []) if now - t < window]
    if len(hits) >= limit:
        wait = window - (now - hits[0])
        raise RateLimitedError(
            f"限流：bucket={bucket} 每 {window}s 上限 {limit}，再等 {wait:.1f}s"
        )
    hits.append(now)
    state[bucket] = hits[-limit:]  # 只保留窗口内的
    _save(state)
    return 0.0


@contextmanager
def acquire(bucket: str):
    check(bucket)
    yield


def status() -> dict[str, dict]:
    """各 bucket 当前状态（监控面板用）。"""
    state = _load()
    now = time.time()
    out = {}
    for bucket, (window, limit) in BUCKETS.items():
        hits = [t for t in state.get(bucket, []) if now - t < window]
        out[bucket] = {
            "recent": len(hits),
            "limit": limit,
            "window_sec": window,
            "next_available_in": max(0, window - (now - hits[0])) if hits else 0,
        }
    return out
