"""Core metadata must not initialize the authentication/transport stack."""
import subprocess
import sys
import unittest


class CoreImportBoundaryTest(unittest.TestCase):
    def run_isolated(self, code):
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_lightweight_modules_do_not_import_session_or_transports(self):
        self.run_isolated("""
import importlib
import sys
for name in ('errors', 'registry', 'strategy', 'price', 'paths'):
    importlib.import_module('goofish_z.core.' + name)
for name in ('goofish_z.core.session', 'goofish_z.core.sign', 'requests',
             'execjs', 'playwright', 'websockets'):
    assert name not in sys.modules, name
""")

    def test_public_session_export_retains_identity(self):
        self.run_isolated("""
import inspect
import goofish_z.core as core
assert 'Session' in core.__all__
assert 'Session' in dir(core)
from goofish_z.core import Session
from goofish_z.core.session import Session as implementation
assert Session is implementation
assert inspect.isclass(Session)
assert core.Session is implementation
try:
    core.unknown_attribute
except AttributeError:
    pass
else:
    raise AssertionError('unknown attribute must raise AttributeError')
""")

    def test_session_import_failures_are_not_hidden(self):
        self.run_isolated("""
import importlib.abc
import sys
import goofish_z.core as core
class BlockSession(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == 'goofish_z.core.session':
            raise ImportError('synthetic dependency failure')
sys.meta_path.insert(0, BlockSession())
try:
    core.Session
except ImportError as error:
    assert str(error) == 'synthetic dependency failure'
else:
    raise AssertionError('Session must propagate import failures')
""")
