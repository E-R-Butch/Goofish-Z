import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from support import OfflineCase
from goofish_z.core.errors import GoofishError, RateLimitedError, RiskControlError


class SafetyTest(OfflineCase):
    def test_writes_share_one_sixty_second_bucket(self):
        self.limiter.check("item.write")
        with self.assertRaises(RateLimitedError) as error:
            self.limiter.check("message.write")
        self.assertGreater(error.exception.retry_after, 59)
        self.assertEqual(self.limiter._bucket_conf("item.write"), (60, 1))

    def test_legacy_reservations_are_preserved(self):
        self.limiter._save({"item.write": [time.time()]})
        with self.assertRaises(RateLimitedError):
            self.limiter.check("write")

    def test_concurrent_requests_cannot_both_take_one_slot(self):
        barrier = Barrier(2)
        original = self.limiter._load
        def slow_read():
            result = original()
            time.sleep(0.05)
            return result
        def attempt(_):
            barrier.wait(timeout=2)
            try:
                self.limiter.check("search")
                return True
            except RateLimitedError:
                return False
        with patch.object(self.limiter, "_load", side_effect=slow_read):
            with ThreadPoolExecutor(max_workers=2) as executor:
                self.assertEqual(sum(executor.map(attempt, range(2))), 1)

    def test_status_uses_configured_capacity(self):
        with patch.dict(os.environ, {"GOOFISH_LIMIT_SEARCH_RPM": "2", "GOOFISH_LIMIT_SEARCH_SEC": "40"}):
            self.limiter.check("search")
            status = self.limiter.status()["search"]
            self.assertEqual(status["limit"], 2)
            self.assertEqual(status["window_sec"], 40)
            self.assertEqual(status["next_available_in"], 0)

    def test_corrupt_state_stops_requests(self):
        self.limiter.STATE_PATH.write_text("{broken")
        with self.assertRaises(GoofishError):
            self.limiter.check("search")

    def test_weaker_circuit_does_not_shorten_active_cooldown(self):
        self.guard.trip("synthetic RGV587")
        before = self.guard._load()["until"]
        self.guard.trip("synthetic 验证码")
        self.assertEqual(self.guard._load()["until"], before)
        self.assertEqual(self.guard.status()["level"], "hard")

    def test_circuit_blocks_search_before_limiting_or_browser(self):
        self.guard.trip("synthetic RGV587")
        with patch.object(self.search, "_run") as browser:
            with self.assertRaises(RiskControlError):
                self.search.search("synthetic")
            browser.assert_not_called()
        self.assertFalse(self.limiter.STATE_PATH.exists())

    def test_circuit_blocks_direct_mtop_and_browser(self):
        from goofish_z.core.mtop import call
        from goofish_z.core.browser import goofish_page
        self.guard.trip("synthetic RGV587")
        with self.assertRaises(RiskControlError):
            call(None, "synthetic.api", {})
        async def browse():
            async with goofish_page(cookies={}):
                self.fail("browser must not open")
        with self.assertRaises(RiskControlError):
            asyncio.run(browse())
