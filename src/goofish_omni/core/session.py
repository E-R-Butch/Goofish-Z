"""会话层 — 请求 + 认证自愈（移植 XianYuApis refresh_token 机制）。

核心创新：token 过期时自动用 _m_h5_tk 前半段重签刷新，不打断调用方。
失败降级链：refresh_token → Chrome cookie 探测 → AuthRequiredError。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import requests
from loguru import logger

from .sign import generate_sign
from .errors import AuthRequiredError

# 数据目录：项目 data/（cookies.json 存这里）
DATA_DIR = Path(os.environ.get("GOOFISH_OMNI_DATA", str(Path.home() / ".goofish-omni")))
COOKIES_PATH = DATA_DIR / "cookies.json"

# 与上游 XianYuApis 一致的 refresh 端点
REFRESH_API = "mtop.taobao.idlemessage.pc.loginuser.get"
APP_KEY = "34839810"


def _load_cookies() -> Optional[dict[str, str]]:
    """加载 cookie 为 {name: value} 字典。"""
    if not COOKIES_PATH.exists():
        return None
    try:
        raw = json.loads(COOKIES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    # 兼容两种格式：dict 或 [{name, value}, ...]
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, list):
        return {str(c.get("name")): str(c.get("value")) for c in raw if c.get("name")}
    return None


def _save_cookies(cookies: dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    COOKIES_PATH.write_text(
        json.dumps(list({"name": k, "value": v} for k, v in cookies.items()), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class GoofishSession:
    """闲鱼 API 会话，带 token 自动刷新。"""

    def __init__(self, cookies: Optional[dict[str, str]] = None) -> None:
        self.session = requests.Session()
        # 兼容上游接口：browser.py 等模块通过 .http 访问 requests session
        self.http = self.session
        self.session.headers.update(
            {
                "accept": "application/json",
                "accept-language": "en,zh-CN;q=0.9,zh;q=0.8",
                "user-agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
                ),
                "origin": "https://www.goofish.com",
                "referer": "https://www.goofish.com/",
            }
        )
        if cookies:
            self.set_cookies(cookies)
        else:
            loaded = _load_cookies()
            if loaded:
                self.set_cookies(loaded)

    # ---- cookie 管理 ----
    def set_cookies(self, cookies: dict[str, str]) -> None:
        for name, value in cookies.items():
            if name and value is not None:
                self.session.cookies.set(name, str(value), domain=".goofish.com", path="/")

    @property
    def cookies_dict(self) -> dict[str, str]:
        return {c.name: c.value for c in self.session.cookies}

    def persist(self) -> None:
        _save_cookies(self.cookies_dict)

    # ---- 核心：带自愈的 mtop 调用 ----
    def call(
        self,
        api: str,
        *,
        data: dict[str, Any] | str = None,
        retry_refresh: bool = True,
        timeout: int = 20,
    ) -> dict[str, Any]:
        """调用 mtop API，token 过期自动刷新重试一次。"""
        data_val = json.dumps(data, ensure_ascii=False, separators=(",", ":")) if data is not None else "{}"
        params = self._build_params(api)
        sign = generate_sign(params["t"], self._token(), data_val)
        params["sign"] = sign

        resp = self.session.post(
            f"https://acs.m.taobao.com/h5/{api}",
            params=params,
            data={"data": data_val},
            timeout=timeout,
        )
        try:
            body = resp.json()
        except Exception:
            raise AuthRequiredError(f"非 JSON 响应 (HTTP {resp.status_code})")

        # 检测登录态失效
        ret = body.get("ret", [""])[0] if isinstance(body.get("ret"), list) else str(body.get("ret", ""))
        if ("FAIL_SYS_TOKEN_EXOIRED" in ret or "FAIL_SYS_USER_VALIDATE" in ret) and retry_refresh:
            logger.warning(f"token 失效 ({ret})，尝试自动刷新...")
            if self.refresh_token():
                logger.info("token 刷新成功，重试原请求")
                return self.call(api, data=data, retry_refresh=False, timeout=timeout)
            raise AuthRequiredError(f"token 刷新失败: {ret}")
        return body

    # ---- 类工厂（兼容上游 Session.load 语义）----
    @classmethod
    def load(cls, cookies: Optional[dict[str, str]] = None) -> "GoofishSession":
        """三级兜底：显式 cookies → cookies.json → 本机 Chrome 自动抓取。"""
        if cookies:
            return cls(cookies=cookies)
        loaded = _load_cookies()
        if loaded:
            return cls(cookies=loaded)
        try:
            import browser_cookie3

            cj = browser_cookie3.chrome(domain_name="goofish.com")
            auto = {c.name: c.value for c in cj}
            if auto:
                logger.info("从本机 Chrome 自动抓取到 cookie")
                _save_cookies(auto)
                return cls(cookies=auto)
        except Exception as e:
            logger.debug(f"Chrome cookie 自动抓取失败: {e}")
        raise AuthRequiredError("未找到有效 cookie，请先执行 goofish-omni auth login")

    # ---- 内部 ----
    def _token(self) -> str:
        tk = self.session.cookies.get("_m_h5_tk")
        return tk.split("_")[0] if tk else ""

    def _build_params(self, api: str) -> dict[str, str]:
        return {
            "jsv": "2.7.2",
            "appKey": APP_KEY,
            "t": str(int(time.time() * 1000)),
            "v": "1.0",
            "type": "originaljson",
            "accountSite": "xianyu",
            "dataType": "json",
            "timeout": "20000",
            "api": api,
            "sessionOption": "AutoLoginOnly",
        }

    def refresh_token(self) -> bool:
        """刷新登录态。返回 True 表示成功。"""
        if not self.cookies_dict.get("unb"):
            return False
        data_val = "{}"
        params = self._build_params(REFRESH_API)
        params["spm_cnt"] = "a21ybx.im.0.0"
        sign = generate_sign(params["t"], self._token(), data_val)
        params["sign"] = sign
        try:
            resp = self.session.post(
                f"https://acs.m.taobao.com/h5/{REFRESH_API}",
                params=params,
                data={"data": data_val},
                timeout=15,
            )
            # 吸收响应中的新 cookie
            for c in resp.cookies:
                self.session.cookies.set(c.name, c.value, domain=".goofish.com", path="/")
            body = resp.json()
            ret = str(body.get("ret", ""))
            ok = "SUCCESS" in ret.upper() or "成功" in ret
            if ok:
                self.persist()
            return ok
        except Exception as e:
            logger.warning(f"refresh_token 异常: {e}")
            return False
