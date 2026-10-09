from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bridge.activity_service import ActivityService
from bridge.config import Settings
from bridge.workspace_registry import WorkspaceRegistry, parse_extra_workspaces


class WorkspaceRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.default_root = base / "default"
        self.docs_root = base / "docs"
        self.default_root.mkdir()
        self.docs_root.mkdir()
        (self.default_root / "same.txt").write_text("default-value\n", encoding="utf-8")
        (self.docs_root / "same.txt").write_text("docs-value\n", encoding="utf-8")
        self.settings = Settings(
            workspace_root=self.default_root,
            allow_write=True,
            allow_commands=True,
            max_read_bytes=262_144,
            max_search_results=500,
            max_search_bytes=262_144,
            max_directory_entries=500,
        )
        self.activity = ActivityService(base / "activity.jsonl")
        self.registry = WorkspaceRegistry(
            self.settings,
            activity=self.activity,
            extra_workspaces=[{"id": "docs", "name": "Docs", "path": str(self.docs_root)}],
            vscode_data_dir=base / "vscode-state",
        )

    def tearDown(self) -> None:
        self.registry.shutdown(timeout=3.0)
        self.temp.cleanup()

    def test_default_is_backward_compatible_and_extra_is_explicit(self) -> None:
        default = self.registry.get()
        docs = self.registry.get("docs")
        self.assertEqual(default.workspace_id, "default")
        self.assertEqual(default.files.read_file("same.txt")["content"], "default-value")
        self.assertEqual(docs.files.read_file("same.txt")["content"], "docs-value")
        self.assertEqual(self.registry.workspace_info()["workspace_id"], "default")
        self.assertEqual(self.registry.workspace_info("docs")["workspace_name"], "Docs")

    def test_paths_cannot_cross_between_workspace_roots(self) -> None:
        with self.assertRaisesRegex(ValueError, "PATH_OUTSIDE_WORKSPACE"):
            self.registry.get("docs").files.read_file("../default/same.txt")

    def test_list_workspaces_is_stable(self) -> None:
        result = self.registry.list_workspaces()
        self.assertEqual(result["default_workspace_id"], "default")
        self.assertEqual(result["count"], 2)
        self.assertEqual([item["workspace_id"] for item in result["workspaces"]], ["default", "docs"])

    def test_unknown_workspace_is_explicit_error(self) -> None:
        with self.assertRaisesRegex(KeyError, "UNKNOWN_WORKSPACE_ID"):
            self.registry.get("missing")

    def test_command_routes_back_to_owning_workspace(self) -> None:
        command = "Write-Output 'docs-command'" if __import__("platform").system() == "Windows" else "printf 'docs-command\\n'"
        result = self.registry.get("docs").terminal.run_command(command, background=False, timeout_ms=10_000)
        routed = self.registry.get_command_output(result["command_id"], 0, 32768)
        self.assertEqual(routed["workspace_id"], "docs")
        self.assertIn("docs-command", routed["output"])
        snapshot = self.registry.list_commands(limit=10)
        self.assertEqual(snapshot["commands"][0]["workspace_id"], "docs")

    def test_parse_extra_workspaces_validates_ids_and_roots(self) -> None:
        parsed = parse_extra_workspaces(json.dumps([{"id": "docs", "path": str(self.docs_root)}]))
        self.assertEqual(parsed[0]["id"], "docs")
        with self.assertRaisesRegex(ValueError, "INVALID_WORKSPACE_ID"):
            parse_extra_workspaces(json.dumps([{"id": "bad id", "path": str(self.docs_root)}]))
        with self.assertRaisesRegex(ValueError, "DUPLICATE_WORKSPACE_ID"):
            parse_extra_workspaces(json.dumps([
                {"id": "docs", "path": str(self.docs_root)},
                {"id": "docs", "path": str(self.default_root)},
            ]))

    def test_overlapping_workspace_roots_are_rejected(self) -> None:
        nested = self.default_root / "nested"
        nested.mkdir()
        with self.assertRaisesRegex(ValueError, "OVERLAPPING_WORKSPACE_ROOT"):
            WorkspaceRegistry(
                self.settings,
                extra_workspaces=[{"id": "nested", "path": str(nested)}],
            )


    def test_workspace_policy_can_only_restrict_global_permissions(self) -> None:
        base = Path(self.temp.name)
        readonly_root = base / "readonly"
        write_root = base / "write-only"
        readonly_root.mkdir()
        write_root.mkdir()
        registry = WorkspaceRegistry(
            self.settings,
            extra_workspaces=[
                {"id": "ro", "path": str(readonly_root), "allow_write": False, "allow_commands": False, "policy_mode": "readonly"},
                {"id": "write", "path": str(write_root), "allow_write": True, "allow_commands": False, "policy_mode": "write"},
            ],
        )
        try:
            ro = registry.get("ro")
            write = registry.get("write")
            self.assertFalse(ro.settings.allow_write)
            self.assertFalse(ro.settings.allow_commands)
            self.assertTrue(write.settings.allow_write)
            self.assertFalse(write.settings.allow_commands)
            with self.assertRaisesRegex(PermissionError, "WRITES_DISABLED"):
                ro.files.write_file("blocked.txt", "x")
            with self.assertRaisesRegex(PermissionError, "COMMANDS_DISABLED"):
                write.terminal.run_command("echo blocked")
            write.files.write_file("allowed.txt", "ok")
            self.assertEqual((write_root / "allowed.txt").read_text(encoding="utf-8"), "ok")
        finally:
            registry.shutdown(timeout=3.0)

    def test_workspace_policy_cannot_elevate_global_readonly(self) -> None:
        base = Path(self.temp.name)
        extra = base / "global-cap"
        extra.mkdir()
        readonly_settings = Settings(
            workspace_root=self.default_root,
            allow_write=False,
            allow_commands=False,
            max_read_bytes=262_144,
            max_search_results=500,
            max_search_bytes=262_144,
            max_directory_entries=500,
        )
        registry = WorkspaceRegistry(
            readonly_settings,
            extra_workspaces=[
                {"id": "full", "path": str(extra), "allow_write": True, "allow_commands": True, "policy_mode": "full"},
            ],
        )
        try:
            context = registry.get("full")
            self.assertFalse(context.settings.allow_write)
            self.assertFalse(context.settings.allow_commands)
            info = registry.list_workspaces()["workspaces"][1]
            self.assertEqual(info["policy_mode"], "full")
            self.assertTrue(info["requested_allow_write"])
            self.assertFalse(info["allow_write"])
            self.assertFalse(info["global_allow_write"])
        finally:
            registry.shutdown(timeout=3.0)

    def test_parse_extra_workspace_rejects_non_boolean_permissions(self) -> None:
        with self.assertRaisesRegex(ValueError, "INVALID_WORKSPACE_ALLOW_WRITE"):
            parse_extra_workspaces(json.dumps([{"id": "docs2", "path": str(self.docs_root), "allow_write": "yes"}]))


if __name__ == "__main__":
    unittest.main()
