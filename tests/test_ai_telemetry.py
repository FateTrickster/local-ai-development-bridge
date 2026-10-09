from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bridge.ai_telemetry import AITelemetryService


class AITelemetryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.service = AITelemetryService(Path(self.temp.name) / "ai-output.jsonl")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_empty_snapshot_is_explicitly_unavailable(self) -> None:
        snapshot = self.service.snapshot()
        self.assertFalse(snapshot["available"])
        self.assertEqual(snapshot["state"], "not_connected")
        self.assertEqual(snapshot["one_minute"]["output_tokens"], 0)
        self.assertIn("does not fabricate TPS", snapshot["note"])

    def test_record_populates_one_and_five_minute_windows(self) -> None:
        event = self.service.record(output_tokens=2660, duration_ms=10_000, source="test", model="demo")
        self.assertEqual(event["tps"], 266.0)
        snapshot = self.service.snapshot()
        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["latest_tps"], 266.0)
        self.assertEqual(snapshot["one_minute"]["output_tokens"], 2660)
        self.assertEqual(snapshot["five_minutes"]["output_tokens"], 2660)
        self.assertEqual(snapshot["source"], "test")
        self.assertEqual(snapshot["model"], "demo")

    def test_invalid_values_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "INVALID_OUTPUT_TOKENS"):
            self.service.record(output_tokens=-1, duration_ms=1000)
        with self.assertRaisesRegex(ValueError, "INVALID_OUTPUT_DURATION"):
            self.service.record(output_tokens=10, duration_ms=0)


if __name__ == "__main__":
    unittest.main()
