from __future__ import annotations

import unittest

from scripts.secret_scan import scan_text


class SecretScanTests(unittest.TestCase):
    def test_detects_capability_url_without_echoing_value(self) -> None:
        findings = scan_text(
            "endpoint=https://demo.trycloudflare.com/AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd/mcp"  # secret-scan: allow
        )
        self.assertEqual(findings, [(1, "capability-url")])

    def test_detects_common_token_prefixes(self) -> None:
        github_fixture = "token=" + "ghp_" + "abcdefghijklmnopqrstuvwxyz123456"  # secret-scan: allow
        openai_fixture = "key=" + "sk-" + "abcdefghijklmnopqrstuvwxyz123456"  # secret-scan: allow
        self.assertEqual(scan_text(github_fixture), [(1, "github-token")])
        self.assertEqual(scan_text(openai_fixture), [(1, "openai-style-key")])

    def test_documented_placeholders_are_ignored(self) -> None:
        self.assertEqual(scan_text("Authorization: Bearer secret-token"), [])
        self.assertEqual(scan_text("https://demo.trycloudflare.com/<capability-token>/mcp"), [])
        self.assertEqual(scan_text("token=[REDACTED]"), [])

    def test_allow_marker_skips_fixture_line(self) -> None:
        self.assertEqual(
            scan_text(
                "token=" + "ghp_" + "abcdefghijklmnopqrstuvwxyz123456  # secret-scan: allow"
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
