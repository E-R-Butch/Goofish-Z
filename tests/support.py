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
        self.use(patch.dict(os.environ, {"GOOFISH_Z_DATA": str(self.root)}))
        self.use(patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden in unit tests")))
        self.use(patch("goofish_z.core.session.Session.load", side_effect=AssertionError("real credentials forbidden")))
        self.watch = importlib.import_module("goofish_z.commands.watch.watch")
        self.search = importlib.import_module("goofish_z.commands.search.search")
        from goofish_z.core import guard, limiter
        self.guard, self.limiter = guard, limiter
        self.use(patch.object(guard, "STATE_PATH", self.root / "circuit.json"))
        self.use(patch.object(limiter, "STATE_PATH", self.root / "limiter.json"))
        self.use(patch.object(self.watch, "DEFAULT_DB", self.root / "watch.db"))
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
