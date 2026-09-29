import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from telemetry import summarize


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.handle = {"id": "AuroraSync", "confidence": "EXTRACTED"}
        self.row = {"handle": "AuroraSync", "environment": "preview", "state": "enabled",
                    "source": "fixture:telemetry", "observed_at": "2026-01-01T00:00:00Z"}

    def test_missing_is_unavailable(self):
        self.assertEqual(summarize([], self.handle)["status"], "unavailable")
        self.assertEqual(summarize([self.row], {**self.handle, "confidence": "INFERRED"})["status"], "unavailable")

    def test_conflict_and_deduplication(self):
        result = summarize([self.row, self.row, {**self.row, "state": "disabled"}], self.handle)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(len(result["observations"]), 2)

    def test_scope_and_required_provenance(self):
        result = summarize([self.row, {**self.row, "handle": "Different"}], self.handle)
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(result["status"], "observed")
        self.assertEqual(summarize([{**self.row, "source": ""}], self.handle)["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
