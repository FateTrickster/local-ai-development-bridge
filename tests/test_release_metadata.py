from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class ReleaseMetadataTests(unittest.TestCase):
    def test_root_and_vscode_versions_match(self) -> None:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        package = json.loads((ROOT / "vscode-companion" / "package.json").read_text(encoding="utf-8"))
        self.assertRegex(version, r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
        self.assertEqual(package["version"], version)

    def test_vscode_package_has_release_metadata(self) -> None:
        package = json.loads((ROOT / "vscode-companion" / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["license"], "MIT")
        self.assertIn("FateTrickster/local-ai-development-bridge", package["repository"]["url"])
        self.assertIn("package", package["scripts"])
        self.assertIn("vscode:prepublish", package["scripts"])

    def test_release_workflow_is_tag_driven(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn('tags:', workflow)
        self.assertIn('"v*"', workflow)
        self.assertIn("gh release", workflow)
        self.assertIn("SHA256SUMS.txt", workflow)


if __name__ == "__main__":
    unittest.main()
