from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bridge.task_service import TaskService


class TaskServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = TaskService(root / "todos.json", root / "progress.jsonl")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_set_todos_and_auto_associate_progress(self) -> None:
        snapshot = self.service.set_todos(
            [
                {"id": "a", "content": "first", "status": "in_progress"},
                {"id": "b", "content": "second", "status": "pending"},
            ]
        )
        self.assertEqual(snapshot["version"], 1)
        event = self.service.report_progress("working", current=1, total=2)
        self.assertEqual(event["todo_id"], "a")
        self.assertEqual(event["seq"], 1)
        state = self.service.get_state()
        self.assertEqual(state["progress_events"], 1)

    def test_progress_events_are_queryable(self) -> None:
        self.service.set_todos([{"id": "a", "content": "first", "status": "in_progress"}])
        self.service.report_progress("one", current=1, total=3)
        self.service.report_progress("two", current=2, total=3)
        self.service.report_progress("three", current=3, total=3)
        tail = self.service.get_progress_events(limit=2)
        self.assertEqual([item["message"] for item in tail["events"]], ["two", "three"])
        self.assertTrue(tail["truncated"])
        after = self.service.get_progress_events(limit=10, after_seq=1)
        self.assertEqual([item["seq"] for item in after["events"]], [2, 3])
        self.assertEqual(after["next_seq"], 3)

    def test_multiple_in_progress_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "MULTIPLE_IN_PROGRESS_TODOS"):
            self.service.set_todos(
                [
                    {"id": "a", "content": "first", "status": "in_progress"},
                    {"id": "b", "content": "second", "status": "in_progress"},
                ]
            )

    def test_unknown_todo_progress_is_rejected(self) -> None:
        self.service.set_todos([{"id": "a", "content": "first", "status": "pending"}])
        with self.assertRaisesRegex(ValueError, "UNKNOWN_TODO_ID"):
            self.service.report_progress("x", todo_id="missing")

    def test_invalid_measured_progress_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "CURRENT_AND_TOTAL_MUST_BE_PAIRED"):
            self.service.report_progress("x", current=1)
        with self.assertRaisesRegex(ValueError, "INVALID_PROGRESS_RANGE"):
            self.service.report_progress("x", current=3, total=2)

    def test_hierarchical_active_chain_and_stage_timing(self) -> None:
        state = self.service.set_todos(
            [
                {"id": "root", "content": "total", "status": "in_progress"},
                {"id": "sub", "content": "sub", "status": "in_progress", "parent_id": "root"},
                {"id": "leaf", "content": "leaf", "status": "in_progress", "parent_id": "sub"},
                {"id": "later", "content": "later", "status": "pending", "parent_id": "root"},
            ]
        )
        self.assertEqual(state["active_leaf_id"], "leaf")
        self.assertEqual(state["active_path"], ["root", "sub", "leaf"])
        by_id = {item["id"]: item for item in state["todos"]}
        self.assertEqual(by_id["root"]["level"], 0)
        self.assertEqual(by_id["sub"]["level"], 1)
        self.assertEqual(by_id["leaf"]["level"], 2)
        self.assertIsNotNone(by_id["root"]["started_at"])
        self.assertIsNotNone(by_id["leaf"]["elapsed_ms"])
        event = self.service.report_progress("nested progress")
        self.assertEqual(event["todo_id"], "leaf")

    def test_in_progress_siblings_are_still_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "MULTIPLE_IN_PROGRESS_TODOS"):
            self.service.set_todos(
                [
                    {"id": "root", "content": "total", "status": "in_progress"},
                    {"id": "a", "content": "a", "status": "in_progress", "parent_id": "root"},
                    {"id": "b", "content": "b", "status": "in_progress", "parent_id": "root"},
                ]
            )

    def test_parent_validation_and_cycle_detection(self) -> None:
        with self.assertRaisesRegex(ValueError, "UNKNOWN_PARENT_ID"):
            self.service.set_todos([{"id": "a", "content": "a", "status": "pending", "parent_id": "missing"}])
        with self.assertRaisesRegex(ValueError, "TODO_PARENT_CYCLE"):
            self.service.set_todos(
                [
                    {"id": "a", "content": "a", "status": "pending", "parent_id": "b"},
                    {"id": "b", "content": "b", "status": "pending", "parent_id": "a"},
                ]
            )

    def test_timestamps_survive_replacement_and_completion(self) -> None:
        first = self.service.set_todos([{"id": "a", "content": "first", "status": "in_progress"}])
        started = first["todos"][0]["started_at"]
        completed = self.service.set_todos([{"id": "a", "content": "first", "status": "completed"}])
        item = completed["todos"][0]
        self.assertEqual(item["started_at"], started)
        self.assertIsNotNone(item["completed_at"])
        self.assertIsNotNone(item["elapsed_ms"])

    def test_legacy_snapshot_is_reported_instead_of_silent_missing_timing(self) -> None:
        self.service.todo_file.write_text(
            '{"version":46,"todos":[{"id":"old","content":"legacy","status":"completed"}],"updated_at":"2026-10-09T08:29:49Z"}',
            encoding="utf-8",
        )
        state = self.service.get_state()
        self.assertEqual(state["schema_version"], 1)
        self.assertFalse(state["timing_available"])
        self.assertEqual(state["timing_state"], "legacy_missing_timestamps")
        self.assertEqual(state["legacy_timing_ids"], ["old"])

    def test_new_snapshot_has_timing_schema_and_live_timestamps(self) -> None:
        state = self.service.set_todos(
            [
                {"id": "root", "content": "root", "status": "in_progress"},
                {"id": "leaf", "content": "leaf", "status": "in_progress", "parent_id": "root"},
            ]
        )
        self.assertEqual(state["schema_version"], 2)
        self.assertTrue(state["timing_available"])
        self.assertEqual(state["timing_state"], "ok")
        by_id = {item["id"]: item for item in state["todos"]}
        self.assertIsNotNone(by_id["root"]["started_at"])
        self.assertIsNotNone(by_id["leaf"]["started_at"])

    def test_completed_chain_remains_available_for_stage_display(self) -> None:
        self.service.set_todos(
            [
                {"id": "root", "content": "root", "status": "in_progress"},
                {"id": "sub", "content": "sub", "status": "in_progress", "parent_id": "root"},
                {"id": "leaf", "content": "leaf", "status": "in_progress", "parent_id": "sub"},
            ]
        )
        state = self.service.set_todos(
            [
                {"id": "root", "content": "root", "status": "completed"},
                {"id": "sub", "content": "sub", "status": "completed", "parent_id": "root"},
                {"id": "leaf", "content": "leaf", "status": "completed", "parent_id": "sub"},
            ]
        )
        self.assertEqual(state["active_path"], [])
        self.assertEqual(state["display_path"], ["root", "sub", "leaf"])
        self.assertEqual(state["display_path_source"], "completed")
        by_id = {item["id"]: item for item in state["todos"]}
        self.assertIsNotNone(by_id["root"]["elapsed_ms"])
        self.assertIsNotNone(by_id["leaf"]["elapsed_ms"])



if __name__ == "__main__":
    unittest.main()
