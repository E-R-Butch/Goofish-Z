"""Offline coverage of the opt-in Chrome test's startup supervision."""
import asyncio
import unittest
from unittest.mock import patch

import test_browser_real_chrome as chrome_test


class BrowserStartupWaitCase(unittest.IsolatedAsyncioTestCase):
    def test_only_missing_executable_is_skippable(self):
        self.assertTrue(chrome_test.chrome_unavailable("Executable doesn't exist: Chrome"))
        self.assertTrue(chrome_test.chrome_unavailable("Chromium distribution 'chrome' is not found at /synthetic/chrome"))
        self.assertFalse(chrome_test.chrome_unavailable("channel disconnected"))
        self.assertFalse(chrome_test.chrome_unavailable("browser launch failed"))

    async def test_missing_chrome_is_propagated_without_polling_to_deadline(self):
        async def fail(_factory):
            raise RuntimeError("Executable doesn't exist: Chrome")

        with patch.object(chrome_test.browser_mod, "run_in_browser", fail):
            with self.assertRaisesRegex(RuntimeError, "Executable doesn't exist"):
                await asyncio.wait_for(chrome_test.hold_then_cancel(None, lambda: {}, 25), 1)

    async def test_snapshot_failure_cancels_and_awaits_worker(self):
        stopped = asyncio.Event()

        async def hold(_factory):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        def fail_snapshot():
            raise ValueError("snapshot failed")

        with patch.object(chrome_test.browser_mod, "run_in_browser", hold):
            with self.assertRaisesRegex(ValueError, "snapshot failed"):
                await chrome_test.hold_then_cancel(None, fail_snapshot)
        self.assertTrue(stopped.is_set())

    async def test_ready_browser_is_cancelled_and_awaited(self):
        stopped = asyncio.Event()

        async def hold(_factory):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        with patch.object(chrome_test.browser_mod, "run_in_browser", hold), patch.object(chrome_test, "pids_for_profile", return_value=[123]):
            result = await chrome_test.hold_then_cancel(None, lambda: {"profile": "synthetic", "alive": True})
        self.assertEqual(result, "synthetic")
        self.assertTrue(stopped.is_set())

    async def test_timeout_cleans_up_worker(self):
        stopped = asyncio.Event()

        async def hold(_factory):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        with patch.object(chrome_test.browser_mod, "run_in_browser", hold):
            with self.assertRaisesRegex(AssertionError, "超时"):
                await chrome_test.hold_then_cancel(None, lambda: {"profile": None, "alive": False}, 0.01)
        self.assertTrue(stopped.is_set())
