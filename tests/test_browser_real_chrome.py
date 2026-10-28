"""Real-Chrome teardown regression (opt-in).

Unit tests never launch a browser, so they cannot catch a cancelled request that
leaves an orphan Chrome behind. This case does launch one, cancels a request
mid-flight, and asserts the process tree for that profile is gone afterwards.

Run with:
    GOOFISH_TEST_REAL_CHROME=1 GOOFISH_HEADLESS=1 \
        python -m unittest discover -s tests -p "test_browser_real_chrome.py"
"""
import asyncio
import os
import subprocess
import time
import unittest
from unittest.mock import patch

from goofish_z.core import browser as browser_mod

ENABLED = os.environ.get("GOOFISH_TEST_REAL_CHROME") == "1"


def pids_for_profile(profile_dir: str) -> list[int]:
    """Chrome processes whose command line points at this profile directory."""
    try:
        out = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        line = line.strip()
        if not line or profile_dir not in line:
            continue
        head = line.split(None, 1)[0]
        if head.isdigit():
            pids.append(int(head))
    return pids


async def hold_then_cancel(open_page_and_hold, snapshot, timeout: float = 25.0):
    """Propagate startup errors promptly and always await worker cancellation."""
    task = asyncio.create_task(browser_mod.run_in_browser(open_page_and_hold))
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if task.done():
                await task  # Preserve missing-Chrome errors for the opt-in skip.
                raise AssertionError("浏览器请求在取消前意外结束")
            state = await asyncio.get_running_loop().run_in_executor(None, snapshot)
            if task.done():
                await task
                raise AssertionError("浏览器请求在取消前意外结束")
            if state["profile"] and state["alive"] and pids_for_profile(state["profile"]):
                return state["profile"]
            # Wake immediately on failure rather than hiding it behind polling.
            await asyncio.wait({task}, timeout=min(0.5, max(0, deadline - time.monotonic())))
        raise AssertionError("常驻 Chrome 未能在超时内启动")
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            # An original startup/snapshot exception is propagated by the try body.
            # Retrieve the task exception here to prevent an unhandled-task warning.
            pass


def chrome_unavailable(message: str) -> bool:
    """Only an absent executable is skippable, not arbitrary channel failures."""
    return "Executable doesn't exist" in message or (
        "Chromium distribution" in message and "is not found at" in message
    )


@unittest.skipUnless(ENABLED, "real Chrome regression (set GOOFISH_TEST_REAL_CHROME=1)")
class RealChromeTeardownCase(unittest.TestCase):
    def setUp(self):
        self.use(patch.object(browser_mod, "_load_cookies_from_session", lambda: {}))
        self.use(patch.dict(os.environ, {"GOOFISH_HEADLESS": "1"}))

    def use(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def snapshot(self) -> dict:
        async def read():
            pool = browser_mod.warm_pool()
            return {
                "profile": str(pool._profile_dir) if pool._profile_dir else None,
                "alive": pool.alive,
                "recycles": pool.recycles,
            }

        return asyncio.run(browser_mod.run_in_browser(read))

    def test_cancelled_request_leaves_no_chrome_behind(self):
        async def open_page_and_hold():
            async with browser_mod.warm_pool().page() as page:
                await page.goto("about:blank")
                await asyncio.Event().wait()  # hold until cancelled


        try:
            profile = asyncio.run(hold_then_cancel(open_page_and_hold, self.snapshot))
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if chrome_unavailable(message):
                self.skipTest(f"runner 上没有可用的 Chrome channel：{message[:120]}")
            raise
        self.assertTrue(profile, "测试期间应拿到 profile 目录")

        deadline = time.monotonic() + 20
        remaining = pids_for_profile(profile)
        while remaining and time.monotonic() < deadline:
            time.sleep(0.5)
            remaining = pids_for_profile(profile)
        self.assertEqual(remaining, [], f"取消后仍有 Chrome 进程存活: {remaining}")

        after = self.snapshot()
        self.assertGreaterEqual(after["recycles"], 1, "取消路径必须回收浏览器")
        self.assertFalse(after["alive"], "取消后池应为空")

        # 回收之后必须还能继续服务（恢复能力，不应把池锁死在坏状态）
        async def reuse():
            async with browser_mod.warm_pool().page() as page:
                await page.goto("about:blank")
                return True

        self.assertTrue(asyncio.run(browser_mod.run_in_browser(reuse)))

        final = self.snapshot()
        pids = pids_for_profile(final["profile"]) if final["profile"] else []
        for pid in pids:  # 收尾：主动回收并确认进程退出
            try:
                os.kill(pid, 15)
            except Exception:
                pass
        asyncio.run(browser_mod.run_in_browser(browser_mod.recycle_warm_browser, "测试收尾"))
