"""The installed package, not a repository-relative copy, owns signing JS."""
import hashlib
import unittest
from importlib.resources import files


class PackagedSigningAssetTest(unittest.TestCase):
    def test_packaged_bridge_is_the_audited_unmodified_asset(self):
        payload = files('goofish_z.static').joinpath('goofish_js_version_2.js').read_bytes()
        # Git blob identity of the existing asset at the cleanup baseline.
        blob = b'blob ' + str(len(payload)).encode() + b'\0' + payload
        self.assertEqual(hashlib.sha1(blob).hexdigest(), '7c8b7d7917887fab3ee5266dd9907ccc7423ab3f')
