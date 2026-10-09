from __future__ import annotations

import argparse
import os
import tempfile
import unittest
from unittest.mock import patch

import launcher


class LauncherHelperTests(unittest.TestCase):
    def test_merge_csv_is_stable_and_deduplicated(self) -> None:
        self.assertEqual(
            launcher._merge_csv("a.example.com,b.example.com", "b.example.com", "c.example.com"),
            "a.example.com,b.example.com,c.example.com",
        )

    def test_server_env_injects_exact_tunnel_host_permissions_and_approvals(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            args = argparse.Namespace(
                workspace=temp,
                readonly=True,
                bridge_port=8123,
                dashboard_port=8877,
                confirm_writes=True,
                confirm_commands=True,
                approval_ttl=180,
            )
            with patch.dict(os.environ, {"BRIDGE_ALLOWED_HOSTS": "existing.example.com"}, clear=False):
                env = launcher._server_env(
                    args,
                    "https://test-host.trycloudflare.com",
                    "test-host.trycloudflare.com",
                )
            self.assertEqual(env["ALLOW_WRITE"], "0")
            self.assertEqual(env["ALLOW_COMMANDS"], "0")
            self.assertEqual(env["BRIDGE_PORT"], "8123")
            self.assertEqual(env["BRIDGE_DASHBOARD_PORT"], "8877")
            self.assertEqual(env["BRIDGE_CONFIRM_WRITES"], "1")
            self.assertEqual(env["BRIDGE_CONFIRM_COMMANDS"], "1")
            self.assertEqual(env["BRIDGE_APPROVAL_TTL_SECONDS"], "180")
            self.assertEqual(env["BRIDGE_ALLOWED_HOSTS"], "test-host.trycloudflare.com")
            self.assertEqual(env["BRIDGE_ALLOWED_ORIGINS"], "https://test-host.trycloudflare.com")
            self.assertNotIn("BRIDGE_TOKEN", {k: v for k, v in env.items() if k not in os.environ})

    def test_local_only_env_removes_stale_tunnel_allowlists(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            args = argparse.Namespace(
                workspace=temp,
                readonly=False,
                bridge_port=8123,
                dashboard_port=8877,
                confirm_writes=False,
                confirm_commands=False,
                approval_ttl=300,
            )
            with patch.dict(
                os.environ,
                {
                    "BRIDGE_ALLOWED_HOSTS": "stale.trycloudflare.com",
                    "BRIDGE_ALLOWED_ORIGINS": "https://stale.trycloudflare.com",
                },
                clear=False,
            ):
                env = launcher._server_env(args)
            self.assertNotIn("BRIDGE_ALLOWED_HOSTS", env)
            self.assertNotIn("BRIDGE_ALLOWED_ORIGINS", env)
            self.assertEqual(env["ALLOW_WRITE"], "1")
            self.assertEqual(env["ALLOW_COMMANDS"], "1")
            self.assertEqual(env["BRIDGE_CONFIRM_WRITES"], "0")
            self.assertEqual(env["BRIDGE_CONFIRM_COMMANDS"], "0")

    def test_port_available_detects_bound_port(self) -> None:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
            self.assertFalse(launcher._port_available("127.0.0.1", port))


if __name__ == "__main__":
    unittest.main()
