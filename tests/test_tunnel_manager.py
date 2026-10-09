from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bridge.tunnel_manager import (
    CloudflareQuickTunnel,
    discover_cloudflared,
    extract_quick_tunnel_url,
    tunnel_hostname,
)


class _FakeProcess:
    def __init__(self, lines: list[str]):
        self.pid = 4321
        self.stdout = iter(lines)
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class TunnelManagerTests(unittest.TestCase):
    def test_extract_and_validate_quick_tunnel_url(self) -> None:
        line = "INF +------------------------------------------------ https://Demo-Name.trycloudflare.com"
        url = extract_quick_tunnel_url(line)
        self.assertEqual(url, "https://Demo-Name.trycloudflare.com")
        self.assertEqual(tunnel_hostname(url), "demo-name.trycloudflare.com")
        self.assertIsNone(
            extract_quick_tunnel_url(
                'failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": timeout'
            )
        )
        with self.assertRaisesRegex(ValueError, "INVALID_TUNNEL_ORIGIN"):
            tunnel_hostname("http://example.com")

    def test_discover_explicit_cloudflared(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cloudflared.exe"
            path.write_bytes(b"test")
            self.assertEqual(discover_cloudflared(str(path)), path.resolve())

    def test_quick_tunnel_reads_public_origin_and_stops(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "cloudflared.exe"
            executable.write_bytes(b"test")
            fake = _FakeProcess(
                [
                    "2026-10-09 INF Requesting new quick Tunnel on trycloudflare.com...\n",
                    "2026-10-09 INF https://unit-test-tunnel.trycloudflare.com\n",
                    "2026-10-09 INF Registered tunnel connection\n",
                ]
            )

            def popen_factory(*args, **kwargs):
                return fake

            tunnel = CloudflareQuickTunnel(
                "http://127.0.0.1:8000",
                executable=executable,
                startup_timeout=3,
                popen_factory=popen_factory,
            )
            status = tunnel.start()
            self.assertTrue(status["running"])
            self.assertEqual(status["hostname"], "unit-test-tunnel.trycloudflare.com")
            self.assertEqual(status["public_origin"], "https://unit-test-tunnel.trycloudflare.com")
            stopped = tunnel.stop()
            self.assertFalse(stopped["running"])
            self.assertEqual(stopped["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
