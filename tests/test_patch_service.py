from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bridge.config import Settings
from bridge.file_service import sha256_file
from bridge.patch_service import PatchService
from bridge.pathguard import WorkspaceGuard


class PatchServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "note.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        self.settings = Settings(
            workspace_root=self.root,
            allow_write=True,
            allow_commands=False,
            max_read_bytes=262_144,
            max_search_results=500,
            max_search_bytes=262_144,
            max_directory_entries=500,
        )
        self.service = PatchService(self.settings, WorkspaceGuard(self.root))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_apply_update_and_return_versions(self) -> None:
        before = sha256_file(self.root / "note.txt")
        patch = """--- a/note.txt
+++ b/note.txt
@@ -1,3 +1,3 @@
 alpha
-beta
+BETA
 gamma
"""
        result = self.service.apply_patch(patch, {"note.txt": before})
        self.assertTrue(result["applied"])
        self.assertEqual((self.root / "note.txt").read_text(encoding="utf-8"), "alpha\nBETA\ngamma\n")
        self.assertEqual(result["files"][0]["before_version"], before)
        self.assertNotEqual(result["files"][0]["after_version"], before)

    def test_stale_version_is_blocked_without_write(self) -> None:
        before_text = (self.root / "note.txt").read_text(encoding="utf-8")
        patch = """--- a/note.txt
+++ b/note.txt
@@ -1,3 +1,3 @@
 alpha
-beta
+BETA
 gamma
"""
        with self.assertRaisesRegex(ValueError, "VERSION_CONFLICT"):
            self.service.apply_patch(patch, {"note.txt": "sha256:" + "0" * 64})
        self.assertEqual((self.root / "note.txt").read_text(encoding="utf-8"), before_text)

    def test_add_file_requires_null_expected_version(self) -> None:
        patch = """--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+one
+two
"""
        result = self.service.apply_patch(patch, {"new.txt": None})
        self.assertTrue(result["applied"])
        self.assertEqual((self.root / "new.txt").read_text(encoding="utf-8"), "one\ntwo\n")

    def test_missing_expected_version_is_rejected(self) -> None:
        patch = """--- a/note.txt
+++ b/note.txt
@@ -1,3 +1,3 @@
 alpha
-beta
+BETA
 gamma
"""
        with self.assertRaisesRegex(ValueError, "EXPECTED_VERSION_REQUIRED"):
            self.service.apply_patch(patch, {})


if __name__ == "__main__":
    unittest.main()
