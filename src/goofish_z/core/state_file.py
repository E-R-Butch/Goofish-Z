"""Process-safe updates for the shared limiter and circuit state."""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock

from goofish_z.core.errors import GoofishError


def file_version(path: Path) -> list[int] | None:
    """Identify a local file revision without reading or hashing its contents."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


@contextmanager
def state_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=10):
        yield


def read_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise GoofishError(f"无法读取 {path.name}，请检查运行状态文件") from exc
    if not isinstance(value, dict):
        raise GoofishError(f"{path.name} 格式错误，已停止请求")
    return value


def write_state(path: Path, value: dict | list) -> None:
    """原子发布一份 JSON 快照：写临时文件 → fsync → 替换。失败时旧文件保持完整。

    临时文件由 mkstemp 创建，权限已是 0600，不会出现「先写后 chmod」的窗口。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
