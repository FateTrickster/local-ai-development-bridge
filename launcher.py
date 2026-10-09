from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from bridge.launcher_state import LauncherStateStore
from bridge.security import get_or_create_token
from bridge.tunnel_manager import CloudflareQuickTunnel, discover_cloudflared


ROOT = Path(__file__).resolve().parent
DEFAULT_BRIDGE_HOST = "127.0.0.1"
DEFAULT_BRIDGE_PORT = 8000
DEFAULT_DASHBOARD_PORT = 8766
REQUIRED_TOOLS = {"workspace_info", "list_directory", "read_file", "get_activity", "dashboard_info"}


def _print(message: str) -> None:
    print(message, flush=True)


def _split_csv(raw: str | None) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _merge_csv(raw: str | None, *values: str) -> str:
    items = _split_csv(raw)
    for value in values:
        value = value.strip()
        if value and value not in items:
            items.append(value)
    return ",".join(items)


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _wait_tcp(host: str, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.15)
    raise TimeoutError(f"BRIDGE_PORT_NOT_READY: {host}:{port}")


def _find_dashboard(port: int, timeout: float = 8.0) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for candidate in range(port, min(65_535, port + 10) + 1):
            url = f"http://127.0.0.1:{candidate}/api/state"
            try:
                with urllib.request.urlopen(url, timeout=0.4) as response:
                    if response.status == 200:
                        payload = json.loads(response.read().decode("utf-8"))
                        if isinstance(payload, dict) and "system" in payload:
                            return f"http://127.0.0.1:{candidate}/"
            except (OSError, urllib.error.URLError, json.JSONDecodeError):
                continue
        time.sleep(0.2)
    return None


async def _mcp_smoke_once(url: str) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            missing = sorted(REQUIRED_TOOLS - names)
            if missing:
                raise RuntimeError(f"MCP_SMOKE_MISSING_TOOLS: {missing}")
            return {
                "server_name": getattr(initialized.serverInfo, "name", None),
                "server_version": getattr(initialized.serverInfo, "version", None),
                "tool_count": len(names),
                "required_tools_ok": True,
            }


def _mcp_smoke(url: str, timeout: float = 20.0) -> dict[str, Any]:
    deadline = time.monotonic() + max(3.0, timeout)
    last_error: Exception | None = None
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        try:
            result = asyncio.run(_mcp_smoke_once(url))
            return {**result, "attempts": attempts}
        except Exception as exc:  # retry while bridge/tunnel settles
            last_error = exc
            time.sleep(0.7)
    raise RuntimeError(f"MCP_SMOKE_FAILED after {attempts} attempts: {last_error}")


def _preflight(args: argparse.Namespace) -> dict[str, Any]:
    if sys.version_info < (3, 11):
        raise RuntimeError("PYTHON_TOO_OLD: Python 3.11+ is required")
    if not (ROOT / "server.py").is_file():
        raise RuntimeError("SERVER_PY_MISSING")
    if args.bridge_port < 1 or args.bridge_port > 65_535:
        raise ValueError("INVALID_BRIDGE_PORT")
    if args.dashboard_port < 1 or args.dashboard_port > 65_535:
        raise ValueError("INVALID_DASHBOARD_PORT")
    if not _port_available(DEFAULT_BRIDGE_HOST, args.bridge_port):
        raise RuntimeError(f"BRIDGE_PORT_IN_USE: {DEFAULT_BRIDGE_HOST}:{args.bridge_port}")
    cloudflared = None
    if not args.no_tunnel:
        cloudflared = str(discover_cloudflared(args.cloudflared))
    try:
        import mcp  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(f"PYTHON_DEPENDENCY_MISSING: {exc}") from exc
    return {
        "python": sys.executable,
        "python_version": ".".join(str(item) for item in sys.version_info[:3]),
        "cloudflared": cloudflared,
        "workspace_root": str(Path(args.workspace).resolve()),
        "bridge_port": args.bridge_port,
        "dashboard_port": args.dashboard_port,
        "tunnel_enabled": not args.no_tunnel,
        "confirm_writes": bool(getattr(args, "confirm_writes", False)),
        "confirm_commands": bool(getattr(args, "confirm_commands", False)),
    }


def _server_env(args: argparse.Namespace, public_origin: str | None = None, hostname: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["WORKSPACE_ROOT"] = str(Path(args.workspace).resolve())
    env["ALLOW_WRITE"] = "0" if args.readonly else "1"
    env["ALLOW_COMMANDS"] = "0" if args.readonly else "1"
    env["BRIDGE_HOST"] = DEFAULT_BRIDGE_HOST
    env["BRIDGE_PORT"] = str(args.bridge_port)
    env["BRIDGE_DASHBOARD_PORT"] = str(args.dashboard_port)
    env["BRIDGE_CONFIRM_WRITES"] = "1" if getattr(args, "confirm_writes", False) else "0"
    env["BRIDGE_CONFIRM_COMMANDS"] = "1" if getattr(args, "confirm_commands", False) else "0"
    env["BRIDGE_APPROVAL_TTL_SECONDS"] = str(getattr(args, "approval_ttl", 300))
    if hostname:
        env["BRIDGE_ALLOWED_HOSTS"] = hostname
    else:
        env.pop("BRIDGE_ALLOWED_HOSTS", None)
    if public_origin:
        env["BRIDGE_ALLOWED_ORIGINS"] = public_origin
    else:
        env.pop("BRIDGE_ALLOWED_ORIGINS", None)
    return env


def _terminate_process(process: subprocess.Popen[Any] | None, timeout: float = 5.0) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def command_doctor(args: argparse.Namespace) -> int:
    try:
        result = _preflight(args)
    except Exception as exc:
        _print(f"[doctor] FAILED: {exc}")
        return 1
    _print("[doctor] environment OK")
    for key, value in result.items():
        _print(f"  {key}: {value}")
    return 0


def command_status(_: argparse.Namespace) -> int:
    state = LauncherStateStore().read()
    _print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


def command_start(args: argparse.Namespace) -> int:
    state = LauncherStateStore()
    tunnel: CloudflareQuickTunnel | None = None
    server_process: subprocess.Popen[Any] | None = None
    public_origin: str | None = None
    hostname: str | None = None
    started_at = time.time()

    state.replace(
        {
            "managed_by_launcher": True,
            "status": "starting",
            "phase": "preflight",
            "mode": "readonly" if args.readonly else "full",
            "tunnel_enabled": not args.no_tunnel,
            "confirm_writes": bool(args.confirm_writes),
            "confirm_commands": bool(args.confirm_commands),
            "approval_ttl_seconds": int(args.approval_ttl),
            "workspace_root": str(Path(args.workspace).resolve()),
            "bridge": {"host": DEFAULT_BRIDGE_HOST, "port": args.bridge_port},
            "dashboard": {"host": "127.0.0.1", "preferred_port": args.dashboard_port, "url": None},
            "tunnel": {"provider": "cloudflare-quick", "running": False, "public_origin": None, "hostname": None},
            "smoke": {"local": None, "public": None},
            "error": None,
        }
    )

    try:
        preflight = _preflight(args)
        state.mark_phase("preflight_complete", preflight=preflight)
        _print("[1/5] Preflight passed")

        if not args.no_tunnel:
            state.mark_phase("tunnel_starting")
            _print("[2/5] Starting Cloudflare Quick Tunnel...")
            tunnel = CloudflareQuickTunnel(
                f"http://127.0.0.1:{args.bridge_port}",
                executable=args.cloudflared,
                startup_timeout=args.startup_timeout,
                on_line=lambda line: _print(f"[cloudflared] {line}"),
            )
            tunnel_state = tunnel.start()
            public_origin = str(tunnel_state["public_origin"])
            hostname = str(tunnel_state["hostname"])
            state.mark_phase(
                "tunnel_ready",
                tunnel={
                    "provider": "cloudflare-quick",
                    "running": True,
                    "pid": tunnel_state.get("pid"),
                    "public_origin": public_origin,
                    "hostname": hostname,
                },
            )
            _print(f"      Tunnel ready: {public_origin}")
        else:
            _print("[2/5] Tunnel skipped (--no-tunnel)")
            state.update(tunnel={"provider": None, "running": False, "public_origin": None, "hostname": None})

        state.mark_phase("bridge_starting")
        _print("[3/5] Starting MCP Bridge...")
        env = _server_env(args, public_origin, hostname)
        server_process = subprocess.Popen([sys.executable, "-u", "server.py"], cwd=ROOT, env=env)
        state.update(bridge={"host": DEFAULT_BRIDGE_HOST, "port": args.bridge_port, "pid": server_process.pid, "running": True})
        _wait_tcp(DEFAULT_BRIDGE_HOST, args.bridge_port, args.startup_timeout)
        dashboard_url = _find_dashboard(args.dashboard_port, timeout=5.0)
        state.mark_phase(
            "bridge_ready",
            bridge={"host": DEFAULT_BRIDGE_HOST, "port": args.bridge_port, "pid": server_process.pid, "running": True},
            dashboard={"host": "127.0.0.1", "preferred_port": args.dashboard_port, "url": dashboard_url},
        )
        _print(f"      Bridge ready: http://127.0.0.1:{args.bridge_port}")
        if dashboard_url:
            _print(f"      Dashboard: {dashboard_url}")

        token = get_or_create_token()
        local_url = f"http://127.0.0.1:{args.bridge_port}/{token}/mcp"
        public_url = f"{public_origin}/{token}/mcp" if public_origin else None

        if not args.no_smoke:
            state.mark_phase("smoke_testing")
            _print("[4/5] Running MCP smoke tests...")
            local_smoke = _mcp_smoke(local_url, timeout=args.smoke_timeout)
            state.update(smoke={"local": {"status": "passed", **local_smoke}, "public": None})
            _print(f"      Local initialize/tools-list passed ({local_smoke['tool_count']} tools)")
            public_smoke = None
            if public_url:
                public_smoke = _mcp_smoke(public_url, timeout=args.smoke_timeout)
                state.update(
                    smoke={
                        "local": {"status": "passed", **local_smoke},
                        "public": {"status": "passed", **public_smoke},
                    }
                )
                _print(f"      Public initialize/tools-list passed ({public_smoke['tool_count']} tools)")
        else:
            _print("[4/5] Smoke tests skipped (--no-smoke)")

        state.mark_phase(
            "ready",
            status="ready",
            ready_at=time.time(),
            bridge={"host": DEFAULT_BRIDGE_HOST, "port": args.bridge_port, "pid": server_process.pid, "running": True},
            tunnel={
                "provider": "cloudflare-quick" if tunnel else None,
                "running": bool(tunnel),
                "pid": tunnel.process.pid if tunnel and tunnel.process else None,
                "public_origin": public_origin,
                "hostname": hostname,
            },
        )
        _print("[5/5] Local AI Development Bridge is ready")
        _print(f"      Local MCP endpoint: {local_url}")
        if public_url:
            _print(f"      Public MCP endpoint: {public_url}")
        _print("      Capability URLs contain a secret. Do not publish or commit them.")

        if args.exit_after_ready:
            return 0

        while True:
            if server_process.poll() is not None:
                raise RuntimeError(f"BRIDGE_EXITED: {server_process.returncode}")
            if tunnel is not None and tunnel.process is not None and tunnel.process.poll() is not None:
                raise RuntimeError(f"TUNNEL_EXITED: {tunnel.process.returncode}")
            time.sleep(0.5)

    except KeyboardInterrupt:
        _print("\nStopping...")
        state.mark_phase("stopping", status="stopping")
        return 0
    except Exception as exc:
        state.mark_phase("failed", status="failed", error={"type": type(exc).__name__, "message": str(exc)})
        _print(f"[launcher] FAILED: {exc}")
        return 1
    finally:
        _terminate_process(server_process)
        if tunnel is not None:
            tunnel.stop()
        final = state.read()
        if final.get("status") != "failed":
            state.mark_phase(
                "stopped",
                status="stopped",
                stopped_at=time.time(),
                uptime_seconds=round(max(0.0, time.time() - started_at), 2),
                bridge={"host": DEFAULT_BRIDGE_HOST, "port": args.bridge_port, "pid": None, "running": False},
                tunnel={
                    "provider": "cloudflare-quick" if not args.no_tunnel else None,
                    "running": False,
                    "pid": None,
                    "public_origin": public_origin,
                    "hostname": hostname,
                },
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local AI Development Bridge launcher")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--workspace", default=str(ROOT.parent), help="Workspace root exposed through MCP")
        target.add_argument("--bridge-port", type=int, default=int(os.environ.get("BRIDGE_PORT", DEFAULT_BRIDGE_PORT)))
        target.add_argument("--dashboard-port", type=int, default=int(os.environ.get("BRIDGE_DASHBOARD_PORT", DEFAULT_DASHBOARD_PORT)))
        target.add_argument("--cloudflared", default=os.environ.get("CLOUDFLARED_PATH") or None)
        target.add_argument("--no-tunnel", action="store_true", help="Run local MCP only; do not start Quick Tunnel")
        target.add_argument("--startup-timeout", type=float, default=30.0)

    start = sub.add_parser("start", help="Start tunnel, bridge, dashboard and smoke tests")
    add_common(start)
    start.add_argument("--readonly", action="store_true", help="Disable file writes and command execution")
    start.add_argument("--confirm-writes", action="store_true", help="Require localhost Dashboard approval for write_file/apply_patch")
    start.add_argument("--confirm-commands", action="store_true", help="Require localhost Dashboard approval before run_command")
    start.add_argument("--approval-ttl", type=int, default=300, help="Approval request lifetime in seconds (30-3600)")
    start.add_argument("--no-smoke", action="store_true", help="Skip initialize/tools-list smoke checks")
    start.add_argument("--smoke-timeout", type=float, default=25.0)
    start.add_argument("--exit-after-ready", action="store_true", help="Stop all child processes after readiness checks; useful for CI")
    start.set_defaults(func=command_start)

    doctor = sub.add_parser("doctor", help="Validate local prerequisites without starting services")
    add_common(doctor)
    doctor.set_defaults(func=command_doctor, readonly=False, confirm_writes=False, confirm_commands=False, approval_ttl=300)

    status = sub.add_parser("status", help="Print the last launcher state snapshot")
    status.set_defaults(func=command_status)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
