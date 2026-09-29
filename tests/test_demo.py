"""Check the public workflow at its material review boundaries."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import demo


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="atlas-public-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.fixture = json.loads((ROOT / "examples/aurora-sync.json").read_text())

    def write_fixture(self):
        path = self.base / "fixture.json"
        path.write_text(json.dumps(self.fixture), encoding="utf-8")
        return path

    def test_complete_workflow_preserves_gates_without_network(self):
        with mock.patch("socket.socket", side_effect=AssertionError("No network permitted")):
            report = demo.run(self.base / "output")
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["edit_counts"]["auto_edit"], 1)
        proposed = self.base / "output/proposed-docs/docs"
        self.assertEqual([p.name for p in proposed.iterdir()], ["sync-guide.md"])
        self.assertEqual(report["fact_count_after_repeat_merge"], 3)
        self.assertFalse(report["production_writes"])
        states = json.loads((self.base / "output/lifecycle.json").read_text())
        self.assertEqual(states["awaiting_approval"]["overall_route"], "approval")
        self.assertTrue((self.base / "output/staged/2610/release-notes.md").is_file())
        self.assertFalse((self.base / "output/state/assignments-seen.json").exists())

    def test_missing_or_wrong_approval_never_stages(self):
        self.fixture["approval"]["role"] = "observer"
        report = demo.run(self.base / "held", self.write_fixture())
        self.assertFalse(report["valid_fixture_approval"])
        self.assertFalse((self.base / "held/staged").exists())
        self.assertIn("approval", (self.base / "held/state/handoff-queue.md").read_text())

    def test_existing_output_is_untouched(self):
        target = self.base / "existing"
        target.mkdir()
        sentinel = target / "sentinel.txt"
        sentinel.write_text("Keep this")
        with self.assertRaises(ValueError):
            demo.run(target)
        self.assertEqual(sentinel.read_text(), "Keep this")

    def test_fixture_paths_cannot_escape_output(self):
        self.fixture["articles"][0]["path"] = "../outside.md"
        with self.assertRaises(ValueError):
            demo.run(self.base / "rejected", self.write_fixture())
        self.assertFalse((self.base / "outside.md").exists())


if __name__ == "__main__":
    unittest.main()
