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


if __name__ == "__main__":
    unittest.main()
