import os
import unittest
from pathlib import Path
from unittest.mock import patch

from goofish_z.core.errors import GoofishError
from goofish_z.core.paths import runtime_data_dir, runtime_data_path


class RuntimeDataPathTest(unittest.TestCase):
    def test_all_runtime_files_share_the_configured_external_root(self) -> None:
        with patch.dict(os.environ, {"GOOFISH_Z_DATA": "/tmp/goofish-z-synthetic"}):
            self.assertEqual(
                runtime_data_path("watch.db"),
                Path("/tmp/goofish-z-synthetic/watch.db").resolve(),
            )

    def test_public_source_checkout_cannot_be_a_runtime_data_root(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with patch.dict(os.environ, {"GOOFISH_Z_DATA": str(repository / "local")}):
            with self.assertRaises(GoofishError):
                runtime_data_dir()

    def test_runtime_filename_cannot_escape_its_root(self) -> None:
        with self.assertRaises(ValueError):
            runtime_data_path("../cookies.json")


if __name__ == "__main__":
    unittest.main()
