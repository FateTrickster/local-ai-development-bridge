from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bridge.config import Settings
from bridge.file_service import FileService, sha256_file
from bridge.pathguard import WorkspaceGuard


class FileServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "a.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        (self.root / "b.py").write_text("print('Alpha')\n# beta\n", encoding="utf-8")
        (self.root / ".hidden.txt").write_text("secret", encoding="utf-8")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "ignored.js").write_text("beta", encoding="utf-8")
        self.settings = Settings(
            workspace_root=self.root,
            allow_write=True,
            allow_commands=False,
            max_read_bytes=262_144,
            max_search_results=500,
            max_search_bytes=262_144,
            max_directory_entries=500,
        )
        self.guard = WorkspaceGuard(self.root)
        self.service = FileService(self.settings, self.guard)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_directory_pagination_and_hidden_filter(self) -> None:
        first = self.service.list_directory(limit=1)
        self.assertEqual(len(first["entries"]), 1)
        self.assertTrue(first["truncated"])
        self.assertIsNotNone(first["next_cursor"])
        second = self.service.list_directory(cursor=first["next_cursor"], limit=10)
        names = [entry["name"] for entry in first["entries"] + second["entries"]]
        self.assertIn("a.txt", names)
        self.assertIn("b.py", names)
        self.assertNotIn(".hidden.txt", names)
        self.assertNotIn("node_modules", names)

    def test_find_and_search_are_bounded(self) -> None:
        found = self.service.find_files(["*.py", "**/*.py"])
        self.assertEqual([item["path"] for item in found["matches"]], ["b.py"])
        searched = self.service.search_files("beta", case_sensitive=False, context_lines=0)
        paths = {item["path"] for item in searched["results"]}
        self.assertEqual(paths, {"a.txt", "b.py"})

    def test_read_returns_raw_sha256_version(self) -> None:
        result = self.service.read_file("a.txt", 2, 3)
        self.assertTrue(result["ok"])
        self.assertEqual(result["content"], "beta\ngamma")
        self.assertEqual(result["version"], sha256_file(self.root / "a.txt"))
        self.assertEqual(result["actual_start_line"], 2)
        self.assertEqual(result["actual_end_line"], 3)

    def test_write_expected_version_blocks_stale_overwrite(self) -> None:
        current = sha256_file(self.root / "a.txt")
        changed = self.service.write_file("a.txt", "new\n", current)
        self.assertNotEqual(changed["after_version"], current)
        with self.assertRaisesRegex(ValueError, "VERSION_CONFLICT"):
            self.service.write_file("a.txt", "stale\n", current)

    def test_path_escape_is_blocked(self) -> None:
        with self.assertRaisesRegex(ValueError, "PATH_OUTSIDE_WORKSPACE"):
            self.service.read_file("../outside.txt")


if __name__ == "__main__":
    unittest.main()
