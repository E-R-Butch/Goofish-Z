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

import asyncio
import os
import shutil
import tempfile
import time
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


_FINGERPRINT_JS = """
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
            await page.add_init_script(_FINGERPRINT_JS)
            try:
                yield page
            finally:
                await context.close()
    finally:
        # 清理 tmp profile。忽略错误（进程被 kill 时残留目录由用户手动清 ~/.goofish-z/profiles/）
        shutil.rmtree(profile_dir, ignore_errors=True)


async def auto_scroll(page: Any, times: int = 2, pause_ms: int = 800) -> None:
    """模拟 OpenCLI 的 `page.autoScroll({times})`：滚到底 N 次触发懒加载。"""
    await page.wait_for_function("Boolean(document.body)", timeout=15000)
    for _ in range(times):
        # 登录跳转可能在等待结束后替换文档，不能假定 body 始终存在。
        await page.evaluate("""() => {
            const body = document.body;
            if (body) window.scrollTo(0, body.scrollHeight);
        }""")
        await page.wait_for_timeout(pause_ms)
    await page.evaluate("window.scrollTo(0, 0)")


# ── 常驻浏览器池（搜索路径专用）────────────────────────────────────────────────
# 为什么：每次搜索冷启 Chrome 实测 1.96s（11 进程 / ~930MB），而搜索页本身要等
# React 渲染 ~4.9s。常驻只省前者，但顺带让指纹更像"一直开着的真实浏览器"——
# 每次全新 profile、零历史、首访即搜索，本身就是异常特征。
#
# 约束：一个 user_data_dir 只能被一个 Chrome 实例打开（SingletonLock），所以池容量为 1，
# 并发调用在锁上排队；搜索本来就被 limiter 串行化（30s/次），不损失吞吐。
# 其它调用方（登录、扫码、详情、watch）继续走 goofish_page() 的"每次独立 profile"，
# 不受影响——并发能力保留在那边。
#
# 安全阀：每 WARM_MAX_USES 次、TTL 到期、风控/登录态异常、new_page 失败 → 销毁重建。
# cookie 每次 acquire 从 Session.load() 热更新，登录态刷新后无需重启服务。

WARM_REUSE = os.environ.get("GOOFISH_BROWSER_REUSE", "1") != "0"
WARM_MAX_USES = int(os.environ.get("GOOFISH_BROWSER_MAX_USES", "50"))
WARM_TTL_SECONDS = float(os.environ.get("GOOFISH_BROWSER_TTL_SECONDS", "1800"))

_WARM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-default-browser-check",
    "--no-first-run",
    # 容器内存上限 2G、Chrome 常驻实测 ~930MB，下面三项压低峰值避免 OOM 拖走 uvicorn
    "--disable-dev-shm-usage",
    "--renderer-process-limit=1",
    "--js-flags=--max-old-space-size=256",
]


class _WarmPool:
    """单实例常驻 Chrome。所有方法都要求调用方已持有 lock（page() 内部保证）。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pw: Any = None
        self._ctx: Any = None
        self._profile_dir: Path | None = None
        self._uses = 0
        self._born = 0.0
        self.recycles = 0

    @property
    def alive(self) -> bool:
        return self._ctx is not None

    async def _start(self) -> None:
        from playwright.async_api import async_playwright

        PROFILES_PARENT.mkdir(parents=True, exist_ok=True)
        profile_dir = Path(tempfile.mkdtemp(prefix="chrome-warm-", dir=str(PROFILES_PARENT)))
        pw = await async_playwright().start()
        try:
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel="chrome",
                headless=os.environ.get("GOOFISH_HEADLESS") == "1",
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                args=_WARM_ARGS,
            )
        except Exception:
            await pw.stop()
            shutil.rmtree(profile_dir, ignore_errors=True)
            raise
        await ctx.add_init_script(_FINGERPRINT_JS)
        self._pw, self._ctx, self._profile_dir = pw, ctx, profile_dir
        self._uses = 0
        self._born = time.monotonic()
        await self._refresh_cookies()
        logger.info("常驻浏览器已启动 profile={} args={}", profile_dir.name, len(_WARM_ARGS))

    async def _refresh_cookies(self) -> None:
        from goofish_z.core.errors import AuthRequiredError

        cookies = _cookies_to_playwright(_load_cookies_from_session())
        try:
            await self._ctx.add_cookies(cookies)
        except Exception as exc:  # noqa: BLE001
            raise AuthRequiredError("浏览器登录态注入失败，请重新导入 Chrome 登录态") from exc

    async def recycle(self, reason: str = "") -> None:
        ctx, pw, profile_dir = self._ctx, self._pw, self._profile_dir
        self._ctx = self._pw = self._profile_dir = None
        self._uses = 0
        self.recycles += 1
        if reason:
            logger.info("常驻浏览器回收：{}", reason)
        if ctx is not None:
            try:
                await ctx.close()
            except Exception:  # noqa: BLE001
                pass
        if pw is not None:
            try:
                await pw.stop()
            except Exception:  # noqa: BLE001
                pass
        if profile_dir is not None:
            shutil.rmtree(profile_dir, ignore_errors=True)

    async def _ensure(self) -> None:
        if self._ctx is not None:
            age = time.monotonic() - self._born
            if self._uses >= WARM_MAX_USES:
                await self.recycle(f"达到复用上限 {self._uses} 次")
            elif age > WARM_TTL_SECONDS:
                await self.recycle(f"超过 TTL {age:.0f}s")
        if self._ctx is None:
            await self._start()
            return
        # 登录态可能被外部刷新过：每次热更新 cookie（42 条，~5ms）
        await self._refresh_cookies()

    @asynccontextmanager
    async def page(self) -> AsyncIterator[Any]:
        async with self._lock:
            await self._ensure()
            self._uses += 1
            try:
                page = await self._ctx.new_page()
            except Exception as exc:  # noqa: BLE001
                logger.warning("常驻浏览器 new_page 失败（{}），重建", type(exc).__name__)
                await self.recycle("new_page 失败")
                await self._start()
                page = await self._ctx.new_page()
            try:
                yield page
            finally:
                try:
                    await page.goto("about:blank", timeout=5000)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await page.close()
                except Exception:  # noqa: BLE001
                    pass


_POOL: _WarmPool | None = None


def warm_pool() -> _WarmPool:
    global _POOL
    if _POOL is None:
        _POOL = _WarmPool()
    return _POOL


@asynccontextmanager
async def warm_page() -> AsyncIterator[Any]:
    """搜索路径用：复用常驻 Chrome；GOOFISH_BROWSER_REUSE=0 时退回每次独立 profile。"""
    if not WARM_REUSE:
        async with goofish_page() as page:
            yield page
        return
    async with warm_pool().page() as page:
        yield page


async def recycle_warm_browser(reason: str = "") -> None:
    """风控/登录态异常时由调用方主动丢弃常驻浏览器。"""
    if _POOL is not None and _POOL.alive:
        await _POOL.recycle(reason)
