from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bridge.activity_service import ActivityService, redact_text


class ActivityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "activity.jsonl"
        self.service = ActivityService(self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_started_and_completed_events_are_persisted(self) -> None:
        activity_id = self.service.start("read_file", "Read file", {"path": "README.md"})
        self.service.finish(
            activity_id,
            tool="read_file",
            title="Read file completed",
            status="completed",
            details={"size_bytes": 123},
        )
        result = self.service.list_events(limit=10)
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(result["events"][0]["type"], "tool_started")
        self.assertEqual(result["events"][1]["type"], "tool_completed")
        self.assertEqual(result["events"][0]["activity_id"], result["events"][1]["activity_id"])
        self.assertGreaterEqual(result["events"][1]["duration_ms"], 0)

    def test_redaction_masks_common_secret_forms(self) -> None:
        token = "A" * 43
        self.service.emit(
            "test",
            status="completed",
            title="secret test",
            details={
                "authorization": "Bearer should-not-appear",
                "url": f"https://example.test/{token}/mcp",
                "command": "TOKEN=my-private-token python app.py",
            },
        )
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("should-not-appear", raw)
        self.assertNotIn(token, raw)
        self.assertNotIn("my-private-token", raw)
        parsed = json.loads(raw.splitlines()[-1])
        self.assertEqual(parsed["details"]["authorization"], "[REDACTED]")
        self.assertIn("/[REDACTED]/mcp", parsed["details"]["url"])

    def test_after_seq_and_tail_limit(self) -> None:
        for index in range(5):
            self.service.emit("event", status="completed", title=f"event-{index}")
        tail = self.service.list_events(limit=2)
        self.assertEqual([item["seq"] for item in tail["events"]], [4, 5])
        self.assertTrue(tail["truncated"])
        after = self.service.list_events(limit=10, after_seq=3)
        self.assertEqual([item["seq"] for item in after["events"]], [4, 5])


    def test_activity_metrics_count_mcp_operations_once(self) -> None:
        first = self.service.start("read_file", "Read file")
        self.service.finish(first, tool="read_file", title="Read file completed", status="completed")
        second = self.service.start("run_command", "Run command")
        self.service.finish(second, tool="run_command", title="Run command completed", status="completed")
        self.service.emit(
            "file_changed",
            status="completed",
            title="file changed",
            component="files",
        )
        metrics = self.service.activity_metrics()
        self.assertTrue(metrics["available"])
        self.assertEqual(metrics["one_minute_operations"], 2)
        self.assertEqual(metrics["five_minute_operations"], 2)
        self.assertEqual(metrics["five_minute_average_per_minute"], 0.4)
        self.assertEqual(metrics["latest_component"], "files")
        self.assertEqual(metrics["state"], "active")
        self.assertLess(metrics["latest_age_seconds"], 5)

    def test_redact_text_bearer(self) -> None:
        self.assertEqual(redact_text("Authorization: Bearer abc123"), "Authorization: [REDACTED]")


if __name__ == "__main__":
    unittest.main()
