import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class OfflineCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="goofish-z-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.use(patch.dict(os.environ, {"GOOFISH_Z_DATA": str(self.root), "GOOFISH_Z_HTTP": "http://127.0.0.1:8787"}))
        self.use(patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden in unit tests")))
        self.use(patch("goofish_z.core.session.Session.load", side_effect=AssertionError("real credentials forbidden")))
        self.watch = importlib.import_module("goofish_z.commands.watch.watch")
        self.search = importlib.import_module("goofish_z.commands.search.search")
        from goofish_z.core import guard, limiter
        self.guard, self.limiter = guard, limiter
        self.use(patch.object(guard, "STATE_PATH", self.root / "circuit.json"))
        self.use(patch.object(limiter, "STATE_PATH", self.root / "limiter.json"))
        self.use(patch.object(self.watch, "DEFAULT_DB", self.root / "watch.db"))
        # 单测永不启动真浏览器：把常驻池关掉，_browser_stage 在调用方 loop 里跑
        from goofish_z.core import browser as browser_module
        self.use(patch.object(browser_module, "WARM_REUSE", False))
        for name in ("blacklist.blacklist", "signals.signals"):
            module = importlib.import_module("goofish_z.commands." + name)
            self.use(patch.object(module, "DEFAULT_DB", self.root / "watch.db"))

    def use(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result


def fixture(price="80", **fields):
    return {"item_id": "0000000000000", "title": "synthetic item", "price": price,
            "badge": "synthetic credit", "url": "https://example.invalid/item", **fields}
