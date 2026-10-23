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
from typing import Any

import requests
from loguru import logger

from goofish_z.core.errors import AuthRequiredError, RateLimitedError, RiskControlError
from goofish_z.core.paths import runtime_data_path
from goofish_z.core.sign import generate_device_id, generate_sign
from goofish_z.core.state_file import state_lock, write_state


def resolve_cookie_path(cookie_path: Path | str | None = None) -> Path:
    if cookie_path is not None:
        return Path(cookie_path)
    return runtime_data_path("cookies.json")


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)

# 兼容上游命名
DEFAULT_COOKIE_PATH = resolve_cookie_path()


@dataclass
class Session:
    http: requests.Session
    unb: str
    tracknick: str
    device_id: str
    # 会话实际加载的文件；刷新要写回同一份，否则多路径部署会写到别的账号文件
    cookie_path: Path | None = None

    @classmethod
    def load(cls, cookie_path: Path | str | None = None, *, allow_bootstrap: bool = True) -> Session:
        """加载会话。allow_bootstrap=False 时只读本地缓存，绝不自动从 Chrome 抓取。

        显式认证检查（auth status）必须用 False：检查不该顺手修复登录态，
        否则「验证失败」会被一次隐式恢复掩盖成成功。
        """
        path = resolve_cookie_path(cookie_path)

        cookies = _load_or_bootstrap_cookies(path, allow_bootstrap=allow_bootstrap)

        if "unb" not in cookies or "_m_h5_tk" not in cookies:
            raise AuthRequiredError(
                f"cookie 缺失 unb / _m_h5_tk，检查 {path} 是否完整（建议先在浏览器登录 "
                f"https://www.goofish.com 后再试 `goofish-z auth login`）"
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
            device_id=_load_or_mint_device_id(cookies["unb"], path.parent / "device_id.json"),
            cookie_path=path,
        )

    @property
    def h5_token(self) -> str:
        raw = self.http.cookies.get("_m_h5_tk", "")
        return raw.split("_")[0] if raw else ""

    # ---- goofish-omni 增强：认证自愈 ----
    def refresh_token(self) -> bool:
        """刷新登录态（移植 XianYuApis）。成功返回 True，并持久化新 cookie。"""
        from goofish_z.core.guard import check as guard_check
        from goofish_z.core.guard import looks_like_risk, trip
        from goofish_z.core.mtop import (
            _raise_for_http_rate_limit, _raise_for_traffic_limit, _retry_after_seconds,
        )

        guard_check()
        from goofish_z.core.refresh import is_enabled

        if not is_enabled():
            # 自动刷新被整体关闭（GOOFISH_AUTO_REFRESH_TOKEN=0）：直接放弃，不静默打请求
            return False
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
            _raise_for_http_rate_limit(resp, api)
            for c in resp.cookies:
                # 先删除同名旧 cookie（requests 对同 domain/path 重复 name 会报错）
                for existing in list(self.http.cookies):
                    if existing.name == c.name:
                        self.http.cookies.clear(existing.domain, existing.path, existing.name)
                self.http.cookies.set(c.name, c.value, domain=".goofish.com", path="/")
            payload = resp.json()
            payload_dict = payload if isinstance(payload, dict) else {}
            ret = str(payload_dict.get("ret", ""))
            if looks_like_risk(ret):
                # 风控不能当普通刷新失败处理：否则 mtop 会接着走浏览器免密刷新重试，
                # 账号受限期间继续打请求。命中即熔断，并按风控上抛终止整条链路。
                trip(f"refresh_token 触发风控：{ret[:120]}", api=api)
                raise RiskControlError(f"[{api}] 刷新登录态时触发风控：{ret}", raw=payload_dict)
            _raise_for_traffic_limit(
                ret, api, payload_dict,
                retry_after=_retry_after_seconds(resp.headers.get("Retry-After")),
            )
            ok = "SUCCESS" in ret.upper() or "成功" in ret
            if ok:
                from goofish_z.core.browser_cookie import _jars_to_entries

                try:
                    # 写回本会话实际加载的文件；持久化失败不能报告完整恢复成功
                    update_cookies_json(resolve_cookie_path(self.cookie_path), _jars_to_entries([resp.cookies]))
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"登录态持久化失败: {type(e).__name__}")
                    return False
            return ok
        except (RiskControlError, RateLimitedError):
            # 风控和限流必须穿透，不能继续浏览器恢复或再次请求。
            raise
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


def _load_or_bootstrap_cookies(path: Path, *, allow_bootstrap: bool = True) -> dict[str, str]:
    """三级兜底：cookies.json → 本机 Chrome 自动抓取 → AuthRequiredError。

    浏览器自动抓取受三重门控：调用方 allow_bootstrap、GOOFISH_AUTO_REFRESH_TOKEN=0、
    GOOFISH_NO_CHROME_BOOTSTRAP=1。认证检查必须能关掉这一步，否则「验证失败」会被
    一次隐式恢复掩盖成成功。
    """
    cached_account = None
    if path.exists():
        try:
            cookies = _load_cookies(path)
            if cookies.get("unb") and cookies.get("_m_h5_tk"):
                return cookies
            # Expired identity is only an account guard, never a usable credential.
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                for c in raw:
                    if isinstance(c, dict) and c.get("name") == "unb" and c.get("value"):
                        cached_account = str(c.get("value"))
                        break
            elif isinstance(raw, dict):
                cached_account = raw.get("unb")
        except (AuthRequiredError, json.JSONDecodeError):
            pass
    from goofish_z.core.refresh import is_enabled

    if not allow_bootstrap:
        raise AuthRequiredError(
            f"缓存登录态缺失或已过期，且本次调用禁止自动导入（{path}）；请执行 goofish-z auth login"
        )
    if not is_enabled() or os.environ.get("GOOFISH_NO_CHROME_BOOTSTRAP") == "1":
        raise AuthRequiredError("缓存登录态缺失或已过期；自动浏览器恢复已禁用，请执行 goofish-z auth login")
    try:
        from goofish_z.core.browser_cookie import extract_goofish_cookies

        _, entries = extract_goofish_cookies(browser="chrome")
        cookies = goofish_cookie_values(entries)
        if cookies.get("unb") and cookies.get("_m_h5_tk"):
            if cached_account and cookies["unb"] != cached_account:
                raise AuthRequiredError("浏览器账号与缓存账号不同；请确认目标账号后执行 goofish-z auth login")
            # cookie2 才是真正的 session token（见 core/refresh.py）：只有 unb/_m_h5_tk
            # 的半套 cookie 覆盖缓存，会让后续加载误判为可用、跳过恢复，鉴权持续失败。
            if not cookies.get("cookie2"):
                logger.debug("Chrome cookie 缺少 cookie2（会话不完整），不覆盖缓存")
            else:
                logger.info("从本机 Chrome 自动抓取到 cookie")
                write_cookies_json(path, entries)
                return cookies
    except AuthRequiredError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Chrome cookie 自动抓取失败: {e}")
    raise AuthRequiredError(f"未找到有效 cookie，请先执行 goofish-z auth login（或检查 {path}）")


def write_cookies_json(path: Path, cookies: dict[str, str] | list[dict[str, Any]]) -> None:
    """写入 cookie 文件。支持两种格式：
    - dict {name: value} — 闲鱼请求 Cookie 头，默认 .goofish.com
    - list [{name,value,domain,path,secure,httpOnly}] — 保留完整字段（扫码路径）

    原子写：中途失败不会留下半个 cookie 文件，旧文件仍然可用。
    """
    if isinstance(cookies, dict):
        payload = [
            {"name": k, "value": v}
            for k, v in cookies.items()
            if k and v is not None
        ]
    else:
        payload = list(cookies)
    write_state(path, payload)


def goofish_cookie_values(cookies: dict[str, str] | list[dict[str, Any]]) -> dict[str, str]:
    """Select the HTTP API's cookies without mixing same-name Taobao credentials."""
    if isinstance(cookies, dict):
        return {str(k): str(v) for k, v in cookies.items() if v is not None}
    host = "h5api.m.goofish.com"
    candidates = []
    for cookie in cookies:
        scope = cookie.get("domain") or ".goofish.com"
        domain = scope.lstrip(".")
        if host != domain and not (scope.startswith(".") and host.endswith("." + domain)):
            continue
        expires = cookie.get("expires")
        if expires is not None and expires > 0 and expires <= _time.time():
            continue
        candidates.append((len(domain), cookie))
    values: dict[str, str] = {}
    for _, cookie in sorted(candidates, key=lambda pair: pair[0]):
        if cookie.get("name") and cookie.get("value"):
            values[cookie["name"]] = cookie["value"]
    return values


def update_cookies_json(path: Path, fresh: list[dict[str, Any]]) -> None:
    """Refresh only matching name/domain/path entries; keep other origins intact.

    读-合并-写整段持锁：并发刷新不会互相覆盖，也不会读到写了一半的文件。
    """
    with state_lock(path):
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if isinstance(existing, dict):
            existing = [{"name": k, "value": v} for k, v in existing.items()]
        merged = {}
        for cookie in [*existing, *fresh]:
            entry = {**cookie, "domain": cookie.get("domain") or ".goofish.com", "path": cookie.get("path") or "/"}
            merged[(entry["name"], entry["domain"], entry["path"])] = entry
        write_cookies_json(path, list(merged.values()))


def _load_or_mint_device_id(unb: str, cache_path: Path | None = None) -> str:
    """device_id 必须在 unb 维度稳定。加锁避免并发进程各自生成一个。"""
    path = cache_path or runtime_data_path("device_id.json")
    with state_lock(path):
        if path.exists():
            try:
                raw = json.loads(path.read_text())
                if raw.get("unb") == unb and raw.get("device_id"):
                    return raw["device_id"]
            except (json.JSONDecodeError, OSError):
                pass
        device_id = generate_device_id(unb)
        write_state(path, {"unb": unb, "device_id": device_id})
        return device_id


def _load_cookies(path: Path) -> dict[str, str]:
    text = path.read_text()
    raw = json.loads(text)
    # 兼容两种格式：list[{name,value}] / dict
    if isinstance(raw, list):
        return goofish_cookie_values(raw)
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    raise AuthRequiredError(f"cookies.json 格式不识别：{path}")
