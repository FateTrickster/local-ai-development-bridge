from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bridge.launcher_state import LauncherStateStore


class LauncherStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "launcher-state.json"
        self.store = LauncherStateStore(self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_missing_state_is_not_managed(self) -> None:
        state = self.store.read()
        self.assertEqual(state["status"], "not_managed")
        self.assertFalse(state["managed_by_launcher"])

    def test_replace_and_update_are_atomic_and_redacted(self) -> None:
        state = self.store.replace(
            {
                "status": "starting",
                "phase": "tunnel_starting",
                "token": "private-token",
                "nested": {"authorization": "Bearer abc123"},
            }
        )
        self.assertEqual(state["token"], "[REDACTED]")
        self.assertEqual(state["nested"]["authorization"], "[REDACTED]")
        updated = self.store.update(status="ready", phase="ready")
        self.assertEqual(updated["status"], "ready")
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("private-token", raw)
        self.assertNotIn("abc123", raw)
        json.loads(raw)


if __name__ == "__main__":
    unittest.main()
