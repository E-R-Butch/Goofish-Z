import importlib.util
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_public_repo.py"
SPEC = importlib.util.spec_from_file_location("check_public_repo", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
privacy_gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(privacy_gate)


class PublicRepositoryGateTest(unittest.TestCase):
    def test_runtime_state_and_business_exports_are_rejected(self) -> None:
        self.assertTrue(privacy_gate.path_violations("runtime/device_id.json"))
        self.assertTrue(privacy_gate.path_violations("snapshot/account-export.json"))
        self.assertTrue(privacy_gate.path_violations("website/public.txt"))

    def test_opaque_or_binary_files_require_explicit_gate_review(self) -> None:
        self.assertIn(
            "unreviewed binary or opaque file type",
            privacy_gate.path_violations("screenshots/listing.png"),
        )

    def test_realistic_long_ids_are_rejected_in_all_public_text(self) -> None:
        realistic_id = "1234567" + "890123"
        self.assertTrue(
            privacy_gate.text_violations("README.md", f"item id: {realistic_id}")
        )
        self.assertFalse(
            privacy_gate.text_violations("tests/example.py", "item id: 0000000000000")
        )


if __name__ == "__main__":
    unittest.main()
