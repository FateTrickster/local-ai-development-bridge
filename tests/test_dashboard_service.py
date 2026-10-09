from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from bridge.activity_service import ActivityService
from bridge.approval_service import ApprovalService
from bridge.config import Settings
from bridge.dashboard_service import DashboardService
from bridge.launcher_state import LauncherStateStore
from bridge.task_service import TaskService


class _FakeTerminal:
    def list_commands(self, limit: int = 20, tail_bytes: int = 4096):
        return {
            "commands": [
                {
                    "workspace_id": "default",
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


class _FakeRegistry:
    def __init__(self):
        self.terminal = _FakeTerminal()
        self.vscode = _FakeVSCode()

    def list_commands(self, limit: int = 20, tail_bytes: int = 4096):
        return self.terminal.list_commands(limit, tail_bytes)

    def vscode_health(self):
        health = self.vscode.health()
        return {
            **health,
            "workspace_id": "default",
            "workspaces": [
                {"workspace_id": "default", **health},
                {
                    "workspace_id": "docs",
                    "provider_state": "not_ready",
                    "provider_state_reason": "VSCODE_WORKSPACE_MISMATCH",
                    "results": [],
                },
            ],
        }

    def list_workspaces(self, *, include_vscode: bool = False):
        items = [
            {
                "workspace_id": "default",
                "name": "Default workspace",
                "workspace_root": "D:/default",
                "allow_write": True,
                "allow_commands": True,
            },
            {
                "workspace_id": "docs",
                "name": "Docs",
                "workspace_root": "D:/docs",
                "allow_write": True,
                "allow_commands": True,
            },
        ]
        if include_vscode:
            health = {item["workspace_id"]: item for item in self.vscode_health()["workspaces"]}
            for item in items:
                item["vscode"] = health[item["workspace_id"]]
        return {"default_workspace_id": "default", "workspaces": items, "count": 2}


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
            confirm_writes=True,
            confirm_commands=True,
            approval_ttl_seconds=120,
        )
        self.activity = ActivityService(root / "activity.jsonl")
        self.tasks = TaskService(root / "todos.json", root / "progress.jsonl")
        self.approvals = ApprovalService(ttl_seconds=120)
        self.launcher_state = LauncherStateStore(root / "launcher-state.json")
        self.launcher_state.replace(
            {
                "status": "ready",
                "phase": "ready",
                "managed_by_launcher": True,
                "tunnel_enabled": True,
                "tunnel": {
                    "provider": "cloudflare-quick",
                    "running": True,
                    "public_origin": "https://dashboard-test.trycloudflare.com",
                    "hostname": "dashboard-test.trycloudflare.com",
                },
                "smoke": {"local": {"status": "passed"}, "public": {"status": "passed"}},
            }
        )
        self.tasks.set_todos([{"id": "a", "content": "dashboard test", "status": "in_progress"}])
        self.tasks.report_progress("working", current=1, total=2)
        self.activity.emit("test", status="completed", title="dashboard event")
        self.registry = _FakeRegistry()
        self.service = DashboardService(
            self.settings,
            self.activity,
            self.tasks,
            self.registry,  # type: ignore[arg-type]
            self.launcher_state,
            self.approvals,
        )

    def tearDown(self) -> None:
        self.service.stop()
        self.temp.cleanup()

    def test_snapshot_contains_observability_approval_and_workspace_sections(self) -> None:
        request = self.approvals.request(
            "write_file",
            {"workspace_id": "default", "path": "a.txt", "content_sha256": "abc"},
            title="write a.txt",
        )
        state = self.service.snapshot()
        self.assertEqual(state["todo_counts"]["in_progress"], 1)
        self.assertEqual(len(state["progress"]["events"]), 1)
        self.assertGreaterEqual(len(state["activity"]["events"]), 1)
        self.assertEqual(state["commands"]["commands"][0]["workspace_id"], "default")
        self.assertEqual(state["commands"]["commands"][0]["status"], "completed")
        self.assertEqual(state["vscode"]["provider_state"], "ready")
        self.assertEqual(state["workspaces"]["count"], 2)
        self.assertEqual(state["system"]["workspace_count"], 2)
        self.assertEqual(state["launcher"]["phase"], "ready")
        self.assertTrue(state["launcher"]["tunnel"]["running"])
        self.assertEqual(state["approvals"]["pending"], 1)
        self.assertEqual(state["approvals"]["requests"][0]["request_id"], request["request_id"])
        self.assertTrue(state["system"]["confirm_writes"])

    def _post_decision(self, base: str, request_id: str, token: str, decision: str, *, origin: str | None = None):
        req = urllib.request.Request(
            f"{base}/api/approvals/{request_id}",
            data=json.dumps({"decision": decision}).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Bridge-Dashboard-Token": token,
                **({"Origin": origin} if origin else {}),
            },
        )
        return urllib.request.urlopen(req, timeout=3)

    def test_local_http_dashboard_session_workspace_api_and_approval_decision(self) -> None:
        request = self.approvals.request(
            "run_command",
            {"workspace_id": "docs", "command": "echo test", "cwd": "."},
            title="run command",
        )
        started = self.service.start()
        self.assertTrue(started["enabled"])
        base = started["url"].rstrip("/")
        with urllib.request.urlopen(base + "/", timeout=3) as response:
            html = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Local Approval Queue", html)
            self.assertIn("Workspaces", html)
            self.assertIn("批准一次", html)
        with urllib.request.urlopen(base + "/api/session", timeout=3) as response:
            session = json.loads(response.read().decode("utf-8"))
            token = session["dashboard_token"]
            self.assertTrue(session["confirm_writes"])
        with urllib.request.urlopen(base + "/api/workspaces", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["count"], 2)
            self.assertEqual(payload["workspaces"][1]["workspace_id"], "docs")
        with urllib.request.urlopen(base + "/api/state", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["approvals"]["pending"], 1)
        with self._post_decision(base, request["request_id"], token, "approve", origin=base) as response:
            payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["status"], "approved")
        self.assertEqual(self.approvals.list_requests()["approved"], 1)

    def test_approval_post_rejects_missing_token_and_cross_origin(self) -> None:
        request = self.approvals.request(
            "write_file",
            {"workspace_id": "default", "path": "a.txt"},
            title="write",
        )
        started = self.service.start()
        base = started["url"].rstrip("/")
        bad = urllib.request.Request(
            f"{base}/api/approvals/{request['request_id']}",
            data=b'{"decision":"approve"}',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(bad, timeout=3)
        self.assertEqual(missing.exception.code, 403)
        with urllib.request.urlopen(base + "/api/session", timeout=3) as response:
            token = json.loads(response.read().decode("utf-8"))["dashboard_token"]
        with self.assertRaises(urllib.error.HTTPError) as cross:
            self._post_decision(base, request["request_id"], token, "approve", origin="http://evil.example")
        self.assertEqual(cross.exception.code, 403)
        self.assertEqual(self.approvals.list_requests()["pending"], 1)


if __name__ == "__main__":
    unittest.main()
