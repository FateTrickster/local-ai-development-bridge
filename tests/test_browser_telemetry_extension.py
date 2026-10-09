from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "browser-telemetry"


class BrowserTelemetryExtensionTests(unittest.TestCase):
    def test_manifest_is_localhost_only_for_bridge_access(self) -> None:
        manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertIn("https://chatgpt.com/*", manifest["content_scripts"][0]["matches"])
        self.assertIn("http://127.0.0.1/*", manifest["host_permissions"])
        self.assertIn("http://localhost/*", manifest["host_permissions"])
        self.assertEqual(
            set(manifest["host_permissions"]),
            {"http://127.0.0.1/*", "http://localhost/*"},
        )

    def test_content_script_uses_estimated_delta_not_message_body_upload(self) -> None:
        script = (EXT / "content-script.js").read_text(encoding="utf-8")
        self.assertIn('data-message-author-role="assistant"', script)
        self.assertIn("chatgpt-browser-estimate", script)
        self.assertIn("output_tokens: delta", script)
        self.assertNotIn("text:", script)
        self.assertNotIn("innerText:", script)

    def test_service_worker_posts_only_numeric_telemetry(self) -> None:
        script = (EXT / "service-worker.js").read_text(encoding="utf-8")
        self.assertIn("X-Bridge-Telemetry-Token", script)
        self.assertIn("length: 11", script)
        self.assertIn("8766 + i", script)
        self.assertIn("output_tokens", script)
        self.assertIn("duration_ms", script)


if __name__ == "__main__":
    unittest.main()
