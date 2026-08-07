"""会话层 — 上游 goofish-cli Session（完整：device_id/cookie 三级兜底）
+ goofish-omni 增强：refresh_token 自愈 + call_with_refresh。

cookie 路径：~/.goofish-z/cookies.json（可用 GOOFISH_Z_DATA 覆盖）。
"""
from __future__ import annotations

import json
import os
import time as _time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests
from loguru import logger

from goofish_z.core.errors import AuthRequiredError
from goofish_z.core.sign import generate_device_id, generate_sign


def resolve_cookie_path(cookie_path: Path | str | None = None) -> Path:
    if cookie_path is not None:
        return Path(cookie_path)
    data_dir = Path(os.environ.get("GOOFISH_Z_DATA", str(Path.home() / ".goofish-z")))
    return data_dir / "cookies.json"


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)

DEVICE_CACHE_PATH = resolve_cookie_path().parent / "device_id.json"

# 兼容上游命名
DEFAULT_COOKIE_PATH = resolve_cookie_path()


@dataclass
class Session:
    http: requests.Session
    unb: str
    tracknick: str
    device_id: str

    @classmethod
    def load(cls, cookie_path: Path | str | None = None) -> "Session":
        path = resolve_cookie_path(cookie_path)

        cookies = _load_or_bootstrap_cookies(path)

        if "unb" not in cookies or "_m_h5_tk" not in cookies:
            raise AuthRequiredError(
                f"cookie 缺失 unb / _m_h5_tk，检查 {path} 是否完整（建议先在浏览器登录 "
                f"https://www.goofish.com 后再试 `goofish-omni auth login`）"
            )
        http = requests.Session()
        for name, value in cookies.items():
            # 先清同名旧 cookie，避免 requests "multiple cookies" 报错
            for existing in list(http.cookies):
                if existing.name == name:
                    http.cookies.clear(existing.domain, existing.path, existing.name)
            http.cookies.set(name, value, domain=".goofish.com", path="/")
        return cls(
            http=http,
            unb=cookies["unb"],
            tracknick=cookies.get("tracknick", ""),
            device_id=_load_or_mint_device_id(cookies["unb"]),
        )

    @property
    def h5_token(self) -> str:
        raw = self.http.cookies.get("_m_h5_tk", "")
        return raw.split("_")[0] if raw else ""

    # ---- goofish-omni 增强：认证自愈 ----
    def refresh_token(self) -> bool:
        """刷新登录态（移植 XianYuApis）。成功返回 True，并持久化新 cookie。"""
        if not self.http.cookies.get("unb"):
            return False

        api = "mtop.taobao.idlemessage.pc.loginuser.get"
        params = {
            "jsv": "2.7.2",
            "appKey": "34839810",
            "t": str(int(_time.time() * 1000)),
            "v": "1.0",
            "type": "originaljson",
            "accountSite": "xianyu",
            "dataType": "json",
            "timeout": "20000",
            "api": api,
            "sessionOption": "AutoLoginOnly",
            "spm_cnt": "a21ybx.im.0.0",
        }
        data_val = "{}"
        sign = generate_sign(params["t"], self.h5_token, data_val)
        params["sign"] = sign
        try:
            resp = self.http.post(
                "https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.loginuser.get/1.0/",
                params=params,
                data={"data": data_val},
                timeout=15,
            )
            for c in resp.cookies:
                # 先删除同名旧 cookie（requests 对同 domain/path 重复 name 会报错）
                for existing in list(self.http.cookies):
                    if existing.name == c.name:
                        self.http.cookies.clear(existing.domain, existing.path, existing.name)
                self.http.cookies.set(c.name, c.value, domain=".goofish.com", path="/")
            ret = str(resp.json().get("ret", ""))
            ok = "SUCCESS" in ret.upper() or "成功" in ret
            if ok:
                write_cookies_json(resolve_cookie_path(), dict(self.http.cookies))
            return ok
        except Exception as e:  # noqa: BLE001
            logger.warning(f"refresh_token 异常: {e}")
            return False

    def call_with_refresh(self, api: str, *, data: dict | None = None, **kwargs) -> dict:
        """带自愈的 mtop 调用：token 过期 → refresh_token → 重试一次。"""
        from goofish_z.core.mtop import call as mtop_call

        try:
            return mtop_call(self, api, data=data or {}, **kwargs)
        except AuthRequiredError as e:
            ret = str(e)
            if "TOKEN_EXOIRED" in ret or "USER_VALIDATE" in ret:
                if self.refresh_token():
                    return mtop_call(self, api, data=data or {}, **kwargs)
            raise


def _load_or_bootstrap_cookies(path: Path) -> dict[str, str]:
    """三级兜底：cookies.json → 本机 Chrome 自动抓取 → AuthRequiredError。"""
    if path.exists():
        try:
            return _load_cookies(path)
        except AuthRequiredError:
            pass
    try:
        import browser_cookie3

        cj = browser_cookie3.chrome(domain_name="goofish.com")
        cookies = {c.name: c.value for c in cj}
        if cookies:
            logger.info("从本机 Chrome 自动抓取到 cookie")
            write_cookies_json(path, cookies)
            return cookies
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Chrome cookie 自动抓取失败: {e}")
    raise AuthRequiredError(f"未找到有效 cookie，请先执行 goofish-omni auth login（或检查 {path}）")


def write_cookies_json(path: Path, cookies: dict[str, str] | list[dict[str, Any]]) -> None:
    """写入 cookie 文件。支持两种格式：
    - dict {name: value} — 自动补 domain 占位（加载时按名字猜域）
    - list [{name,value,domain,path,secure,httpOnly}] — 保留完整字段（扫码路径）
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(cookies, dict):
        payload = [
            {"name": k, "value": v}
            for k, v in cookies.items()
            if k and v is not None
        ]
    else:
        payload = list(cookies)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _load_or_mint_device_id(unb: str) -> str:
    """device_id 必须在 unb 维度稳定。"""
    if DEVICE_CACHE_PATH.exists():
        try:
            raw = json.loads(DEVICE_CACHE_PATH.read_text())
            if raw.get("unb") == unb and raw.get("device_id"):
                return raw["device_id"]
        except (json.JSONDecodeError, OSError):
            pass
    device_id = generate_device_id(unb)
    DEVICE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEVICE_CACHE_PATH.write_text(json.dumps({"unb": unb, "device_id": device_id}))
    DEVICE_CACHE_PATH.chmod(0o600)
    return device_id


def _load_cookies(path: Path) -> dict[str, str]:
    text = path.read_text()
    raw = json.loads(text)
    # 兼容两种格式：list[{name,value}] / dict
    if isinstance(raw, list):
        return {c["name"]: c["value"] for c in raw if "name" in c and "value" in c}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    raise AuthRequiredError(f"cookies.json 格式不识别：{path}")
