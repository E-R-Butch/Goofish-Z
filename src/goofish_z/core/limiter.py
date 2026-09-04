"""分 bucket 限流 — 不同操作不同频率上限。

参考 ai-goofish-monitor 实战参数 + 上游 goofish-cli 令牌桶：
- search      : 30s 一次（搜索列表，轻）
- detail      : 5s 一次（商品详情，中）
- write       : 60s 一次（发布/发消息，重）
- seller_page : 4s 一次（卖家昵称页面抓取）

状态文件：~/.goofish-z/limiter.json（进程间共享）
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager

from goofish_z.core.errors import RateLimitedError
from goofish_z.core.paths import runtime_data_dir
from goofish_z.core.state_file import read_state, state_lock, write_state

DATA_DIR = runtime_data_dir()
STATE_PATH = DATA_DIR / "limiter.json"

# bucket → (窗口秒数, 窗口内上限)。默认即安全值。
BUCKETS: dict[str, tuple[int, int]] = {
    "search": (30, 1),        # 30s 1 次搜索
    "detail": (5, 1),         # 5s 1 次详情
    "seller_page": (4, 1),    # 4s 1 次页面抓取
    "write": (60, 1),         # 60s 1 次写操作
}


def _canonical_bucket(bucket: str) -> str:
    if bucket in ("item.write", "message.write", "media.write"):
        return "write"
    if bucket not in BUCKETS:
        raise ValueError(f"未知限流 bucket: {bucket}")
    return bucket


def _bucket_conf(bucket: str) -> tuple[int, int]:
    bucket = _canonical_bucket(bucket)
    window, limit = BUCKETS[bucket]
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
    state = read_state(STATE_PATH)
    # Keep recent reservations made by earlier versions under legacy names.
    for legacy in ("item.write", "message.write", "media.write"):
        if legacy in state:
            state.setdefault("write", []).extend(state.pop(legacy))
    return state


def _save(state: dict[str, list[float]]) -> None:
    write_state(STATE_PATH, state)


def check(bucket: str) -> float:
    """原子地消耗一个令牌；超限抛出带 retry_after 的异常。"""
    bucket = _canonical_bucket(bucket)
    window, limit = _bucket_conf(bucket)
    with state_lock(STATE_PATH):
        now = time.time()
        state = _load()
        hits = sorted(t for t in state.get(bucket, []) if now - t < window)
        if len(hits) >= limit:
            wait = window - (now - hits[-limit])
            raise RateLimitedError(
                f"限流：bucket={bucket} 每 {window}s 上限 {limit}，再等 {wait:.1f}s",
                retry_after=max(0.0, wait),
            )
        hits.append(now)
        state[bucket] = hits
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
    for bucket in BUCKETS:
        window, limit = _bucket_conf(bucket)
        hits = sorted(t for t in state.get(bucket, []) if now - t < window)
        out[bucket] = {
            "recent": len(hits),
            "limit": limit,
            "window_sec": window,
            "next_available_in": max(0, window - (now - hits[-limit])) if len(hits) >= limit else 0,
        }
    return out
