"""execjs 桥接 goofish_js_version_2.js。提供 sign/device_id/mid/uuid/decrypt。"""
from __future__ import annotations

import subprocess
from functools import lru_cache, partial
from importlib.resources import files

import execjs
import execjs._external_runtime as _execjs_runtime

# Keep UTF-8 for the JavaScript bridge without changing the process-wide Popen
# class, which MCP and binary subprocess callers rely on.
_execjs_runtime.Popen = partial(subprocess.Popen, encoding="utf-8")


@lru_cache(maxsize=1)
def _ctx() -> execjs._abstract_runtime.AbstractRuntimeContext:
    js_path = files("goofish_z.static").joinpath("goofish_js_version_2.js")
    return execjs.compile(js_path.read_text(encoding="utf-8"))


def generate_sign(t: str, token: str, data: str) -> str:
    return _ctx().call("generate_sign", t, token, data)


def generate_device_id(user_id: str) -> str:
    return _ctx().call("generate_device_id", user_id)


def generate_mid() -> str:
    return _ctx().call("generate_mid")


def generate_uuid() -> str:
    return _ctx().call("generate_uuid")


def decrypt(data: str) -> str:
    return _ctx().call("decrypt", data)
