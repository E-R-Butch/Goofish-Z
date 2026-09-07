"""Normal Chrome profiles for read-only collection; no stealth or challenge bypass."""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from filelock import FileLock, Timeout as LockTimeout

from goofish_z.core.paths import runtime_data_dir
from .models import PLATFORMS, parse_price
from .urls import DOMAINS, HOME, allowed_document, check_platform, product_url, search_url

EXTRACT = Path(__file__).with_name("extract.js").read_text()


class CollectionError(Exception):
    def __init__(self, status: str, message: str, retry_after: int = 0, diagnostics: dict | None = None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after
        self.diagnostics = diagnostics or {}


def profile_root(platform: str) -> Path:
    check_platform(platform)
    root = runtime_data_dir() / "price-sessions" / platform
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root


@asynccontextmanager
async def market_page(platform: str, *, reserve: bool = True):
    from playwright.async_api import async_playwright

    root = profile_root(platform)
    lock = FileLock(str(root / "profile.lock"))
    try:
        lock.acquire(timeout=0)
    except LockTimeout as exc:
        raise CollectionError("busy", "该平台正被采集或登录窗口占用，请稍后再试") from exc
    try:
        stamp = root / "last-read"
        if reserve:
            try:
                remaining = 30 - (time.time() - float(stamp.read_text()))
            except FileNotFoundError:
                remaining = 0
            except ValueError as exc:
                raise CollectionError("failed", "采集间隔文件损坏，请检查本地数据") from exc
            if remaining > 0:
                raise CollectionError("rate_limited", "该平台两次采集至少间隔 30 秒", int(remaining) + 1)
            stamp.write_text(str(time.time()))
            stamp.chmod(0o600)
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                str(root / "chrome"), channel="chrome", headless=False,
                viewport={"width":1280,"height":900}, locale="zh-CN",
                timeout=30000,
            )
            imported = False
            try:
                # Imported cookies are scoped to this platform and never printed.
                cookies = root / "imported-cookies.json"
                applied = root / "cookies-applied"
                needs_import = cookies.exists() and (not applied.exists() or cookies.stat().st_mtime_ns > applied.stat().st_mtime_ns)
                if needs_import:
                    entries = json.loads(cookies.read_text())
                    if not isinstance(entries, list) or any(not cookie_allowed(platform, c) for c in entries):
                        raise CollectionError("failed", "平台登录态格式或域名不正确")
                    await context.add_cookies(entries)
                    imported = True
                page = await context.new_page()
                async def navigation_gate(route):
                    request = route.request
                    if request.is_navigation_request() and request.frame == page.main_frame and not allowed_document(platform, request.url):
                        await route.abort()
                    else:
                        await route.continue_()
                await page.route("**/*", navigation_gate)
                yield page
            finally:
                await context.close()
                if imported:
                    applied.touch(mode=0o600)
    finally:
        lock.release()


def cookie_allowed(platform: str, entry: dict) -> bool:
    host = str(entry.get("domain", "")).lstrip(".").lower()
    return isinstance(entry.get("value"), str) and bool(entry.get("name")) and any(host == d or host.endswith("." + d) for d in DOMAINS[platform])


def import_chrome(platform: str) -> dict:
    """Explicit opt-in only: read this platform's Chrome cookies into private data."""
    import browser_cookie3

    root = profile_root(platform)
    entries = {}
    with FileLock(str(root / "profile.lock"), timeout=0):
        for domain in DOMAINS[platform]:
            for c in browser_cookie3.chrome(domain_name=domain):
                entry = {"name":c.name,"value":c.value,"domain":c.domain,"path":c.path or "/",
                         "secure":bool(c.secure), "httpOnly":c.has_nonstandard_attr("HttpOnly") or c.has_nonstandard_attr("HTTPOnly")}
                if c.expires:
                    entry["expires"] = c.expires
                if not c.is_expired() and cookie_allowed(platform, entry):
                    entries[c.name,c.domain,c.path] = entry
        if not entries:
            raise CollectionError("login_required", "Chrome 中未找到该平台登录态，请先登录")
        target = root / "imported-cookies.json"
        # Set mode before writing credentials, including the first write.
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(list(entries.values()), stream)
        target.chmod(0o600)
    return {"platform":platform,"status":"imported","cookies":len(entries),"message":"已导入本机私有目录；登录有效性以实际采集为准"}


def login(platform: str, timeout_seconds: int = 300) -> dict:
    """User performs login in a dedicated profile; closing its tab finishes."""
    check_platform(platform)
    if not 30 <= timeout_seconds <= 600:
        raise ValueError("登录等待时间须为 30 到 600 秒")

    async def run():
        from playwright.async_api import TimeoutError as BrowserTimeout
        async with market_page(platform, reserve=False) as page:
            await page.goto(HOME[platform], wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_event("close", timeout=timeout_seconds * 1000)
            except BrowserTimeout:
                return {"platform":platform,"status":"timeout","message":"登录等待结束；有效性仍需实际采集验证"}
        return {"platform":platform,"status":"closed","message":"登录窗口已关闭；可运行 price collect 验证"}
    return asyncio.run(run())


async def collect_page(platform: str, query: str, limit: int, url: str = "") -> dict:
    from playwright.async_api import Error as BrowserError, TimeoutError as BrowserTimeout

    target = product_url(platform, url)[0] if url else search_url(platform, query)
    try:
        async with market_page(platform) as page:
            response = await page.goto(target, wait_until="domcontentloaded", timeout=30000)
            if response and response.status >= 400:
                raise CollectionError("access_failed", f"平台页面返回 HTTP {response.status}，未获得价格")
            payload = None
            for _ in range(40):
                try:
                    payload = await page.evaluate(EXTRACT, {"platform":platform,"limit":limit,"detail":bool(url)})
                except BrowserError as error:
                    if "Execution context was destroyed" not in str(error):
                        raise
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    continue
                diagnostics = payload.get("diagnostics", {})
                if payload["verification"]:
                    raise CollectionError("verification_required", f"{PLATFORMS[platform]}要求安全验证，请在浏览器处理后重新采集", diagnostics=diagnostics)
                if payload["login"]:
                    raise CollectionError("login_required", f"{PLATFORMS[platform]}要求登录，请登录后导入该平台登录态", diagnostics=diagnostics)
                # Cards often render before their prices. Keep the bounded wait
                # until every observed card has a usable price, then preserve
                # any still-unpriced cards for an honest partial result.
                if payload["items"] and all(parse_price(item.get("price_text", ""))[0] is not None for item in payload["items"]):
                    break
                if payload["empty"] and not payload["items"]:
                    break
                await page.wait_for_timeout(500)
            if not payload or not payload["items"] and not payload["empty"]:
                diagnostics = (payload or {}).get("diagnostics", {})
                if set(diagnostics.get("login_signals", [])).intersection({"密码登录", "短信登录", "扫码登录", "登录后即可"}) and not diagnostics.get("visible_product_nodes"):
                    raise CollectionError("login_required", f"{PLATFORMS[platform]}显示登录入口且没有商品卡片，请先登录后重试", diagnostics=diagnostics)
                raise CollectionError("parse_failed", "页面未提供可识别的商品卡片，可能尚未加载或页面结构已变化", diagnostics=diagnostics)
            if not url and payload["source_query"].strip() != query.strip():
                raise CollectionError("source_mismatch", "页面关键词与本次采集不一致，已丢弃结果")
            if url and product_url(platform, page.url)[0] != target:
                raise CollectionError("source_mismatch", "商品页面与指定链接不一致，已丢弃结果")
            return payload
    except CollectionError:
        raise
    except BrowserTimeout as exc:
        raise CollectionError("timeout", "页面打开或加载超时，本次未获得价格") from exc
    except BrowserError as exc:
        raise CollectionError("failed", "Chrome 采集失败；请检查浏览器安装和平台页面是否可打开") from exc
