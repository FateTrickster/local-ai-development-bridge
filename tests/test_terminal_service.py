from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from bridge.config import Settings
from bridge.pathguard import WorkspaceGuard
from bridge.terminal_service import TerminalService


@unittest.skipUnless(__import__("platform").system() == "Windows", "Windows PTY integration test")
class TerminalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = Settings(
            workspace_root=self.root,
            allow_write=True,
            allow_commands=True,
            max_read_bytes=262_144,
            max_search_results=500,
            max_search_bytes=262_144,
            max_directory_entries=500,
        )
        self.service = TerminalService(self.settings, WorkspaceGuard(self.root))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_foreground_command_returns_output(self) -> None:
        result = self.service.run_command("Write-Output 'hello-pty'", background=False, timeout_ms=10_000)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("hello-pty", result["output"])

    def test_background_command_can_be_polled(self) -> None:
        result = self.service.run_command(
            "Write-Output 'first'; Start-Sleep -Milliseconds 400; Write-Output 'second'",
            background=True,
        )
        command_id = result["command_id"]
        deadline = time.time() + 10
        final = result
        while time.time() < deadline:
            self.service.wait(command_id, timeout_ms=1_000)
            final = self.service.get_command_output(command_id, 0, 32_768)
            if final["status"] != "running":
                break
        self.assertEqual(final["status"], "completed")
        self.assertIn("first", final["output"])
        self.assertIn("second", final["output"])

    def test_interactive_input(self) -> None:
        command = "$x = Read-Host; Write-Output ('got:' + $x)"
        result = self.service.run_command(command, background=True)
        command_id = result["command_id"]
        time.sleep(0.2)
        sent = self.service.send_command_input(command_id, "abc", append_newline=True)
        self.assertEqual(sent["input_seq"], 1)
        deadline = time.time() + 10
        final = result
        while time.time() < deadline:
            self.service.wait(command_id, timeout_ms=1_000)
            final = self.service.get_command_output(command_id, 0, 32_768)
            if final["status"] != "running":
                break
        self.assertEqual(final["status"], "completed")
        self.assertIn("got:abc", final["output"].replace("\r", ""))

    def test_command_snapshot_is_bounded_and_redacted(self) -> None:
        self.service.run_command("Write-Output 'TOKEN=private-value'", background=False, timeout_ms=10_000)
        snapshot = self.service.list_commands(limit=10, tail_bytes=4096)
        self.assertEqual(snapshot["total_tracked"], 1)
        item = snapshot["commands"][0]
        self.assertEqual(item["status"], "completed")
        self.assertNotIn("private-value", item["command_preview"])
        self.assertNotIn("private-value", item["output_tail"])
        self.assertIn("[REDACTED]", item["output_tail"])


if __name__ == "__main__":
    unittest.main()
