from __future__ import annotations

import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path
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
                extra_workspace=[],
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
            self.assertNotIn("BRIDGE_WORKSPACES_JSON", env)
            self.assertNotIn("BRIDGE_TOKEN", {k: v for k, v in env.items() if k not in os.environ})

    def test_server_env_serializes_extra_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            default = root / "default"
            docs = root / "docs"
            code = root / "code"
            default.mkdir()
            docs.mkdir()
            code.mkdir()
            args = argparse.Namespace(
                workspace=str(default),
                extra_workspace=[f"docs={docs}", f"code={code}"],
                readonly=False,
                bridge_port=8123,
                dashboard_port=8877,
                confirm_writes=False,
                confirm_commands=False,
                approval_ttl=300,
            )
            env = launcher._server_env(args)
            parsed = json.loads(env["BRIDGE_WORKSPACES_JSON"])
            self.assertEqual([item["id"] for item in parsed], ["docs", "code"])
            self.assertEqual(Path(parsed[0]["path"]).resolve(), docs.resolve())
            self.assertEqual(env["WORKSPACE_ROOT"], str(default.resolve()))

    def test_extra_workspace_rejects_invalid_and_duplicate_default_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root.mkdir(exist_ok=True)
            bad = argparse.Namespace(workspace=str(root), extra_workspace=["missing-separator"])
            with self.assertRaisesRegex(ValueError, "INVALID_EXTRA_WORKSPACE"):
                launcher._extra_workspace_specs(bad)
            duplicate = argparse.Namespace(workspace=str(root), extra_workspace=[f"same={root}"])
            with self.assertRaisesRegex(ValueError, "OVERLAPPING_WORKSPACE_ROOT"):
                launcher._extra_workspace_specs(duplicate)

    def test_local_only_env_removes_stale_tunnel_allowlists(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            args = argparse.Namespace(
                workspace=temp,
                extra_workspace=[],
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
                    "BRIDGE_WORKSPACES_JSON": '[{"id":"stale","path":"C:/stale"}]',
                },
                clear=False,
            ):
                env = launcher._server_env(args)
            self.assertNotIn("BRIDGE_ALLOWED_HOSTS", env)
            self.assertNotIn("BRIDGE_ALLOWED_ORIGINS", env)
            self.assertNotIn("BRIDGE_WORKSPACES_JSON", env)
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
