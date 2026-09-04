"""Playwright 浏览器上下文 —— 吸纳 OpenCLI 的浏览器自动化路线。

设计要点（用户明确要求）：
1. **用系统 Chrome**（`channel="chrome"`），不用 playwright 自带的 bundled chromium——
   bundled chromium 的 UA / CDP 指纹太"裸"，是风控高危目标；系统 Chrome 是真实用户
   每天在用的可执行，配上真实 cookies 后基本等同正常浏览。
2. **每次调用独立 profile**（`~/.goofish-z/profiles/chrome-<tmp>/`）：Chrome 一个
   `user_data_dir` 同时只能被一个进程打开（`SingletonLock`），固定路径会让并发调用
   （MCP 同时跑多个 tool / 用户手动并发）直接 ProfileInUse 起不来。所以每次 tmp 一个
   profile，退出清理——代价是首次启动多几百 ms，收益是天然支持并发。登录态不需要靠
   profile 持久化，我们每次用 `add_cookies` 从 `Session.load()` 灌。cookie 来源复用
   `Session.load()` 的三级兜底 —— `cookies.json` → `browser_cookie3` 自动从本机
   Chrome 抓 → `AuthRequiredError`，不在这里重复实现。
3. **默认 headful**：实测 headless chrome 的指纹（即便 channel=chrome）仍会被闲鱼判
   「非法访问」，返回"请使用正常浏览器访问"。要通过就必须以窗口模式启动。CI / 无桌面
   场景可 `GOOFISH_HEADLESS=1` 切回 headless（代价是可能被风控）。

同步命令怎么用：用 `asyncio.run(...)` 驱动本模块的 async 上下文（参考 list_chats
`--watch-secs` 的 `asyncio.run(collect_session_cids(...))` 模式）。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from loguru import logger

from goofish_z.core.session import Session
from goofish_z.core.paths import runtime_data_path

PROFILES_PARENT = runtime_data_path("profiles")

def _cookies_to_playwright(cookies: dict[str, str] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 cookie 转成 playwright `add_cookies` 需要的列表形态。

    支持两种输入：
    - dict {name: value} — 闲鱼请求的 Cookie 头，归属 .goofish.com
    - list [{name,value,domain,path,secure,httpOnly}] — 保留扫码时的原始 domain
      （关键：cookie2/_m_h5_tk 等淘系 cookie 实际来自 .goofish.com 或 .taobao.com，
      猜错域会导致登录态不被识别——2026-08-08 实测修复）
    """
    out: list[dict[str, Any]] = []

    if isinstance(cookies, dict):
        entries = [
            {"name": n, "value": v}
            for n, v in cookies.items()
            if v
        ]
    else:
        entries = [c for c in cookies if c.get("value")]

    for c in entries:
        name = c.get("name")
        if not name:
            continue
        domain = c.get("domain") or ".goofish.com"
        entry = {
            "name": name,
            "value": c.get("value"),
            "domain": domain,
            "path": c.get("path", "/"),
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure": bool(c.get("secure", True)),
        }
        if c.get("expires") is not None:
            entry["expires"] = c["expires"]
        if c.get("sameSite") in ("Lax", "Strict", "None"):
            entry["sameSite"] = c["sameSite"]
        out.append(entry)
    return out


def _load_cookies_from_session() -> dict[str, str] | list[dict[str, Any]]:
    """读取 cookie：优先从 cookies.json 原始格式（保留 domain），
    否则走 Session.load 三级兜底后展平。
    """
    from goofish_z.core.session import resolve_cookie_path

    path = resolve_cookie_path()
    if path.exists():
        try:
            import json as _json

            raw = _json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, list) and raw:
                # 完整字段格式（扫码写入）——保留 domain
                return raw
        except Exception:  # noqa: BLE001
            pass
    session = Session.load()
    return {name: value for name, value in session.http.cookies.items() if value}


@asynccontextmanager
async def goofish_page(
    *,
    headless: bool | None = None,
    viewport: tuple[int, int] = (1280, 800),
    cookies: dict[str, str] | list[dict[str, Any]] | None = None,
) -> AsyncIterator[Any]:
    """启动系统 Chrome（独立 tmp profile）+ 灌 cookie，yield 出一个 `Page`。

    `cookies` 可选：显式传入 `{name: value}` 时直接用；不传则走 `Session.load()`
    三级兜底。自动刷 `_m_h5_tk` 的调用方需要用内存里当前 session 的 cookies 而非
    磁盘快照（可能已被改）—— 传 `cookies=session.http.cookies` 展平后的 dict。

    用法：
        async with goofish_page() as page:
            await page.goto("https://www.goofish.com/search?q=foo")
            ...
    """
    from goofish_z.core.guard import check as guard_check
    guard_check()
    from playwright.async_api import async_playwright

    if headless is None:
        # 默认 headful。CI 用户显式 GOOFISH_HEADLESS=1 切回（可能触发风控）。
        headless = os.environ.get("GOOFISH_HEADLESS") == "1"

    if cookies is None:
        cookies = _load_cookies_from_session()
    pw_cookies = _cookies_to_playwright(cookies)
    PROFILES_PARENT.mkdir(parents=True, exist_ok=True)
    # Resolve authentication before creating a profile so failed login leaves no directory.
    profile_dir = Path(tempfile.mkdtemp(prefix="chrome-", dir=str(PROFILES_PARENT)))

    try:
        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel="chrome",
                headless=headless,
                viewport={"width": viewport[0], "height": viewport[1]},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-default-browser-check",
                    "--no-first-run",
                ],
            )
            try:
                await context.add_cookies(pw_cookies)
            except Exception as e:  # noqa: BLE001
                from goofish_z.core.errors import AuthRequiredError
                await context.close()
                raise AuthRequiredError("浏览器登录态注入失败，请重新导入 Chrome 登录态") from e

            page = context.pages[0] if context.pages else await context.new_page()
            # 隐藏自动化特征（指纹层）：
            # 1. navigator.webdriver（已有）
            # 2. navigator.plugins / languages / hardwareConcurrency（headless 常暴露）
            # 3. window.chrome 对象（Playwright 启动的 Chrome 可能缺失）
            # 4. CDP 检测标记（window.cdc_ 等）
            await page.add_init_script(
                """
                // 1. webdriver 标志
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

                // 2. plugins（真实 Chrome 有 PDF 插件）
                if (navigator.plugins && navigator.plugins.length === 0) {
                    const fakePlugin = () => ({
                        name: 'PDF Viewer', filename: 'internal-pdf-viewer',
                        description: 'Portable Document Format', length: 1,
                        item: () => null, namedItem: () => null,
                    });
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [fakePlugin(), fakePlugin(), fakePlugin(), fakePlugin(), fakePlugin()],
                    });
                }
                // languages 补全（中文环境）
                if (!navigator.languages || navigator.languages.length === 0) {
                    Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en-US']});
                }
                // hardwareConcurrency 常见值 8（Mac 常见）
                Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});

                // 3. window.chrome 对象（Playwright 启动有时缺失）
                if (!window.chrome) {
                    window.chrome = { runtime: {}, loadTimes: () => ({}), csi: () => ({}) };
                }

                // 4. CDP 检测痕迹（window.cdc_ 前缀对象）
                for (const key of Object.keys(window)) {
                    if (key.startsWith('cdc_')) { delete window[key]; }
                }

                // 5. Permissions API 打补丁（headless 常被标记）
                if (window.navigator && window.navigator.permissions && window.navigator.permissions.query) {
                    const origQuery = window.navigator.permissions.query.bind(window.navigator.permissions);
                    window.navigator.permissions.query = (p) =>
                        p && p.name === 'notifications'
                            ? Promise.resolve({state: Notification.permission})
                            : origQuery(p);
                }
                """
            )
            try:
                yield page
            finally:
                await context.close()
    finally:
        # 清理 tmp profile。忽略错误（进程被 kill 时残留目录由用户手动清 ~/.goofish-z/profiles/）
        shutil.rmtree(profile_dir, ignore_errors=True)


async def auto_scroll(page: Any, times: int = 2, pause_ms: int = 800) -> None:
    """模拟 OpenCLI 的 `page.autoScroll({times})`：滚到底 N 次触发懒加载。"""
    for _ in range(times):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(pause_ms)
    await page.evaluate("window.scrollTo(0, 0)")
