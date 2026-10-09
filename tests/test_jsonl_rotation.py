from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bridge.activity_service import ActivityService
from bridge.jsonl_utils import JsonlRotationPolicy, rotate_before_append, rotated_paths
from bridge.security import AuditLogger
from bridge.task_service import TaskService


class JsonlRotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.policy = JsonlRotationPolicy(max_bytes=220, backup_count=2)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_rotation_keeps_bounded_backups_in_chronological_order(self) -> None:
        path = self.root / "events.jsonl"
        path.write_text("a" * 180, encoding="utf-8")
        self.assertTrue(rotate_before_append(path, 80, self.policy))
        path.write_text("new", encoding="utf-8")
        self.assertEqual([item.name for item in rotated_paths(path, self.policy)], ["events.jsonl.1", "events.jsonl"])
        self.assertEqual((self.root / "events.jsonl.1").read_text(encoding="utf-8"), "a" * 180)

    def test_activity_events_survive_rotation_and_seq_remains_monotonic(self) -> None:
        path = self.root / "activity.jsonl"
        service = ActivityService(path, self.policy)
        for index in range(8):
            service.emit("test", status="completed", title=f"event-{index}", details={"payload": "x" * 40})
        self.assertTrue((self.root / "activity.jsonl.1").is_file())
        recent = service.list_events(limit=20)["events"]
        self.assertGreater(len(recent), 1)
        seqs = [int(item["seq"]) for item in recent]
        self.assertEqual(seqs, sorted(seqs))
        gap = service.list_events(limit=20, after_seq=1)
        self.assertTrue(gap["history_lost"])
        self.assertGreater(gap["earliest_seq"], 2)
        restarted = ActivityService(path, self.policy)
        event = restarted.emit("test", status="completed", title="after-restart")
        self.assertGreater(event["seq"], max(seqs))

    def test_progress_rotation_preserves_query_and_sequence(self) -> None:
        todo = self.root / "todos.json"
        progress = self.root / "progress.jsonl"
        service = TaskService(todo, progress, self.policy)
        service.set_todos([{"id": "a", "content": "rotation", "status": "in_progress"}])
        for index in range(8):
            service.report_progress(f"progress-{index}-" + "x" * 35, current=index + 1, total=8)
        self.assertTrue((self.root / "progress.jsonl.1").is_file())
        events = service.get_progress_events(limit=20)["events"]
        self.assertGreater(len(events), 1)
        seqs = [int(item["seq"]) for item in events]
        self.assertEqual(seqs, sorted(seqs))
        gap = service.get_progress_events(limit=20, after_seq=1)
        self.assertTrue(gap["history_lost"])
        self.assertGreater(gap["earliest_seq"], 2)
        restarted = TaskService(todo, progress, self.policy)
        event = restarted.report_progress("after restart")
        self.assertGreater(event["seq"], max(seqs))

    def test_audit_logger_rotates_without_changing_jsonl_shape(self) -> None:
        path = self.root / "requests.jsonl"
        logger = AuditLogger(path, self.policy)
        for index in range(8):
            logger.write("request_allowed", method="POST", path="/mcp", client=f"client-{index}-" + "x" * 25)
        segments = rotated_paths(path, self.policy)
        self.assertGreater(len(segments), 1)
        for segment in segments:
            for line in segment.read_text(encoding="utf-8").splitlines():
                parsed = json.loads(line)
                self.assertEqual(parsed["event"], "request_allowed")


if __name__ == "__main__":
    unittest.main()
