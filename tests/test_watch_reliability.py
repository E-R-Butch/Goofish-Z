import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from support import OfflineCase, fixture
from goofish_z.core.errors import AuthRequiredError, RateLimitedError, RiskControlError
from goofish_z.db import WatchDB
from goofish_z.blacklist import BlacklistDB
from goofish_z.signals import SellerSignalDB


class WatchTest(OfflineCase):
    def setUp(self):
        super().setUp()
        self.db = WatchDB(self.watch.DEFAULT_DB)
        self.wid = self.db.add_watch("synthetic", max_price=100)

    def test_batch_waits_then_completes_every_keyword(self):
        self.db.add_watch("synthetic second")
        self.db.add_watch("synthetic third")
        clock = [1000.0]
        starts = []
        async def fetch(*args):
            starts.append(clock[0])
            return [fixture()]
        def sleep(delay):
            clock[0] += delay
        with patch.object(self.limiter.time, "time", side_effect=lambda: clock[0]), \
             patch.object(self.watch.time, "sleep", side_effect=sleep), \
             patch.object(self.search, "_run", side_effect=fetch):
            result = self.watch.watch_run(all=True)
        self.assertEqual(result["succeeded"], 3)
        self.assertEqual(result["failed"], 0)
        self.assertTrue(all(b-a >= 30 for a,b in zip(starts, starts[1:])))

    def test_monitor_target_is_unambiguous_for_cli_and_mcp(self):
        with patch.object(self.search, "search") as search:
            for params in ({}, {"all": True, "watch_id": self.wid}):
                with self.subTest(params=params), self.assertRaises(ValueError):
                    self.watch.watch_run(**params)
            search.assert_not_called()

    def test_cancellation_interrupts_limiter_wait(self):
        cancel = Event()
        def progress(state):
            if state["phase"] == "waiting":
                cancel.set()
        with patch.object(self.search, "search", side_effect=RateLimitedError("synthetic", retry_after=30)) as search:
            result = self.watch.run_watches(all=True, cancel=cancel, progress=progress)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(search.call_count, 1)
        self.assertEqual(self.db.history(self.wid), [])

    def test_auth_or_risk_failure_stops_remaining_keywords(self):
        self.db.add_watch("synthetic second")
        for error in (AuthRequiredError, RiskControlError):
            with self.subTest(error=error.__name__):
                with patch.object(self.search, "search", side_effect=error("synthetic")) as search:
                    result = self.watch.watch_run(all=True)
                self.assertEqual(search.call_count, 1)
                self.assertEqual(result["failed"], 1)
                self.assertEqual(result["skipped"], 1)
                self.assertIsNone(self.db.get_watch(self.wid)["last_check_at"])

    def test_alerts_repeat_only_after_price_change_or_reentry(self):
        counts = [len(self.db.record_poll(self.wid, [fixture(str(price))])) for price in (80,80,70,110,70)]
        self.assertEqual(counts, [1,0,1,0,1])
        self.assertEqual(len(self.db.recent_alerts()), 3)

    def test_parallel_polls_do_not_duplicate_alerts(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.db.record_poll(self.wid,[fixture()]), range(2)))
        self.assertEqual(sum(map(len, results)), 1)

    def test_read_alert_and_remove_watch(self):
        alert = self.db.record_poll(self.wid,[fixture()])[0]
        self.assertTrue(self.db.mark_alert_read(alert["id"]))
        self.assertEqual(self.db.recent_alerts(unread_only=True), [])
        self.assertEqual(len(self.db.recent_alerts()), 1)
        self.db.remove_watch(self.wid)
        self.assertEqual(self.db.recent_alerts(), [])
        self.assertEqual(self.db.history(self.wid), [])

    def test_latest_per_item_breaks_same_second_ties(self):
        with patch("goofish_z.db.time.time", return_value=1000):
            self.db.record_poll(self.wid,[fixture("80")])
            self.db.record_poll(self.wid,[fixture("70")])
        latest = self.db.latest_per_item(self.wid)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["price"], 70)

    def test_auto_banned_sellers_are_filtered_and_unban_clears_score(self):
        signals = SellerSignalDB(self.watch.DEFAULT_DB)
        for item_id in ("synthetic-a", "synthetic-b"):
            signals.record_signals("synthetic seller",item_id,"synthetic",["low_price_trap","no_badge"])
        blacklist = BlacklistDB(self.watch.DEFAULT_DB)
        item = fixture(seller_nick="synthetic seller")
        passed, blocked = blacklist.filter_items([item])
        self.assertEqual(passed, [])
        self.assertEqual(len(blocked), 1)
        signals.unban("synthetic seller")
        self.assertEqual(signals.get_profile("synthetic seller")["total_score"], 0)
        self.assertEqual(len(blacklist.filter_items([item])[0]), 1)

    def test_new_auto_ban_applies_before_history_and_alerts(self):
        items = [fixture("10", item_id="synthetic-a", seller_nick="synthetic seller", badge="", title="synthetic 代拍"),
                 fixture("10", item_id="synthetic-b", seller_nick="synthetic seller", badge="", title="synthetic 代拍")]
        with patch.object(self.search,"search",return_value={"items":items}):
            result = self.watch.watch_run(watch_id=self.wid)
        self.assertEqual(result["results"][0]["captured"], 0)
        self.assertEqual(self.db.history(self.wid), [])
        self.assertEqual(self.db.recent_alerts(), [])

    def test_low_price_tags_work_without_manual_rules(self):
        passed, blocked = BlacklistDB(self.watch.DEFAULT_DB).filter_items([fixture("10"),fixture("100"),fixture("110")])
        self.assertFalse(blocked)
        self.assertIn("_price_flag", passed[0])
