from __future__ import annotations

import json
import tempfile
import unittest
import urllib.request
from pathlib import Path

from bridge.activity_service import ActivityService
from bridge.config import Settings
from bridge.dashboard_service import DashboardService
from bridge.task_service import TaskService


class _FakeTerminal:
    def list_commands(self, limit: int = 20, tail_bytes: int = 4096):
        return {
            "commands": [
                {
                    "command_id": "cmd-1",
                    "command_preview": "python -m unittest",
                    "cwd": ".",
                    "status": "completed",
                    "exit_code": 0,
                    "started_at": "2026-10-09T00:00:00Z",
                    "elapsed_ms": 25,
                    "output_tail": "OK",
                    "output_lost": False,
                }
            ],
            "total_tracked": 1,
        }


class _FakeVSCode:
    def health(self):
        return {
            "provider_state": "ready",
            "provider_state_reason": "test companion",
            "ready": True,
            "workspace_folders": [],
            "open_documents": [],
        }


class DashboardServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(
            workspace_root=root,
            allow_write=True,
            allow_commands=True,
            max_read_bytes=262_144,
            max_search_results=500,
            max_search_bytes=262_144,
            max_directory_entries=500,
            dashboard_port=0,
        )
        self.activity = ActivityService(root / "activity.jsonl")
        self.tasks = TaskService(root / "todos.json", root / "progress.jsonl")
        self.tasks.set_todos([{"id": "a", "content": "dashboard test", "status": "in_progress"}])
        self.tasks.report_progress("working", current=1, total=2)
        self.activity.emit("test", status="completed", title="dashboard event")
        self.service = DashboardService(
            self.settings,
            self.activity,
            self.tasks,
            _FakeTerminal(),  # type: ignore[arg-type]
            _FakeVSCode(),  # type: ignore[arg-type]
        )

    def tearDown(self) -> None:
        self.service.stop()
        self.temp.cleanup()

    def test_snapshot_contains_observability_sections(self) -> None:
        state = self.service.snapshot()
        self.assertEqual(state["todo_counts"]["in_progress"], 1)
        self.assertEqual(len(state["progress"]["events"]), 1)
        self.assertGreaterEqual(len(state["activity"]["events"]), 1)
        self.assertEqual(state["commands"]["commands"][0]["status"], "completed")
        self.assertEqual(state["vscode"]["provider_state"], "ready")

    def test_local_http_dashboard_and_state_api(self) -> None:
        started = self.service.start()
        self.assertTrue(started["enabled"])
        self.assertIsNotNone(started["port"])
        base = started["url"].rstrip("/")
        with urllib.request.urlopen(base + "/", timeout=3) as response:
            html = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Local AI Development Bridge", html)
        with urllib.request.urlopen(base + "/api/state", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["task"]["todos"][0]["id"], "a")
            self.assertEqual(payload["system"]["dashboard_host"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
