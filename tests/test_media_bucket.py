"""限流桶契约：媒体上传独立预算，经营类写操作仍是 1 次/分钟。

状态文件用临时目录隔离，不碰 ~/.goofish-z/limiter.json。
"""
from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from goofish_z.core import limiter
from goofish_z.core.errors import RateLimitedError


class MediaBucketTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.state = self.tmp / "limiter.json"
        self._patch = patch.object(limiter, "STATE_PATH", self.state)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        # 状态文件旁边还有 state_lock 的 .lock 文件，整目录删。
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_legacy_names_canonicalise(self) -> None:
        self.assertEqual(limiter._canonical_bucket("media.write"), "media")
        self.assertEqual(limiter._canonical_bucket("item.write"), "write")
        self.assertEqual(limiter._canonical_bucket("message.write"), "write")
        self.assertEqual(limiter._canonical_bucket("media"), "media")

    def test_unknown_bucket_still_rejected(self) -> None:
        with self.assertRaises(ValueError):
            limiter._canonical_bucket("nope")

    def test_media_allows_nine_uploads_per_minute(self) -> None:
        for _ in range(9):
            limiter.check("media.write")
        with self.assertRaises(RateLimitedError) as ctx:
            limiter.check("media.write")
        self.assertGreater(ctx.exception.retry_after or 0, 0)

    def test_write_still_one_per_minute(self) -> None:
        limiter.check("item.write")
        with self.assertRaises(RateLimitedError):
            limiter.check("message.write")

    def test_media_and_write_budgets_are_independent(self) -> None:
        limiter.check("media.write")
        limiter.check("item.write")  # 不应被媒体桶消耗影响
        state = limiter._load()
        self.assertEqual(len(state.get("media", [])), 1)
        self.assertEqual(len(state.get("write", [])), 1)

    def test_legacy_media_reservations_migrate_to_media_bucket(self) -> None:
        now = time.time()
        limiter.write_state(self.state, {"media.write": [now], "item.write": [now]})
        state = limiter._load()
        self.assertIn("media", state)
        self.assertIn("write", state)
        self.assertNotIn("media.write", state)
        self.assertNotIn("item.write", state)

    def test_status_reports_media_bucket(self) -> None:
        limiter.check("media.write")
        status = limiter.status()
        self.assertIn("media", status)
        self.assertEqual(status["media"]["recent"], 1)
        self.assertEqual(status["media"]["limit"], 9)


if __name__ == "__main__":
    unittest.main()
