import json
from unittest.mock import patch

from typer.testing import CliRunner

from support import OfflineCase


class CliTest(OfflineCase):
    def test_failed_and_partial_runs_have_nonzero_exit_with_structured_output(self):
        from goofish_z.cli import app

        for status in ("failed", "partial"):
            with self.subTest(status=status), patch.object(
                self.watch, "run_watches", return_value={"status": status, "results": []}
            ):
                result = CliRunner().invoke(app, ["watch", "run", "--all", "--format", "json"])
                self.assertEqual(result.exit_code, 1, result.output)
                self.assertEqual(json.loads(result.stdout)["status"], status)
