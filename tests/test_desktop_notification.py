from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bridge.desktop_notification import DesktopNotificationService


class DesktopNotificationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.calls: list[tuple[str, str]] = []

        def backend(title: str, message: str):
            self.calls.append((title, message))
            return {"launched": True, "backend": "test", "pid": 123}

        self.service = DesktopNotificationService(
            enabled=True,
            state_file=self.root / "notification-state.json",
            backend=backend,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _state(status: str = "completed", *, content: str = "总任务", completed_at: str = "2026-10-09T00:01:00Z"):
        return {
            "version": 2,
            "updated_at": completed_at,
            "todos": [
                {
                    "id": "root",
                    "content": content,
                    "status": status,
                    "parent_id": None,
                    "started_at": "2026-10-09T00:00:00Z",
                    "completed_at": completed_at if status == "completed" else None,
                    "elapsed_ms": 60_000,
                }
            ],
        }

    def test_incomplete_plan_does_not_notify(self) -> None:
        result = self.service.evaluate_task_completion(self._state("in_progress"))
        self.assertFalse(result["notified"])
        self.assertEqual(result["reason"], "TASKS_STILL_ACTIVE")
        self.assertEqual(self.calls, [])

    def test_completed_plan_notifies_once(self) -> None:
        state = self._state()
        first = self.service.evaluate_task_completion(state)
        second = self.service.evaluate_task_completion(state)
        self.assertTrue(first["notified"])
        self.assertEqual(first["backend"], "test")
        self.assertFalse(second["notified"])
        self.assertEqual(second["reason"], "ALREADY_NOTIFIED")
        self.assertEqual(len(self.calls), 1)
        self.assertIn("所有任务已经完成", self.calls[0][1])
        self.assertIn("总用时：1 分 00 秒", self.calls[0][1])

    def test_deduplication_survives_service_restart(self) -> None:
        state = self._state()
        self.assertTrue(self.service.evaluate_task_completion(state)["notified"])

        calls_after_restart: list[tuple[str, str]] = []

        def backend(title: str, message: str):
            calls_after_restart.append((title, message))
            return {"launched": True, "backend": "test"}

        restarted = DesktopNotificationService(
            enabled=True,
            state_file=self.root / "notification-state.json",
            backend=backend,
        )
        result = restarted.evaluate_task_completion(state)
        self.assertFalse(result["notified"])
        self.assertEqual(result["reason"], "ALREADY_NOTIFIED")
        self.assertEqual(calls_after_restart, [])

    def test_distinct_completed_plan_notifies_again(self) -> None:
        self.assertTrue(self.service.evaluate_task_completion(self._state(content="任务 A"))["notified"])
        second = self.service.evaluate_task_completion(
            self._state(content="任务 B", completed_at="2026-10-09T00:02:00Z")
        )
        self.assertTrue(second["notified"])
        self.assertEqual(len(self.calls), 2)

    def test_disabled_service_never_launches_backend(self) -> None:
        disabled = DesktopNotificationService(
            enabled=False,
            state_file=self.root / "disabled.json",
            backend=lambda title, message: {"launched": True, "backend": "test"},
        )
        result = disabled.evaluate_task_completion(self._state())
        self.assertFalse(result["notified"])
        self.assertEqual(result["reason"], "DESKTOP_NOTIFICATIONS_DISABLED")

    def test_backend_failure_does_not_mark_completion_as_notified(self) -> None:
        failing = DesktopNotificationService(
            enabled=True,
            state_file=self.root / "failing.json",
            backend=lambda title, message: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        result = failing.evaluate_task_completion(self._state())
        self.assertFalse(result["notified"])
        self.assertEqual(result["reason"], "NOTIFICATION_BACKEND_FAILED")
        self.assertFalse((self.root / "failing.json").exists())


if __name__ == "__main__":
    unittest.main()
