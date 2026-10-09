from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .activity_service import ActivityService
from .config import Settings
from .launcher_state import LauncherStateStore
from .task_service import TaskService
from .terminal_service import TerminalService
from .vscode_service import VSCodeService


_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_HTML = _PACKAGE_ROOT / "dashboard" / "index.html"


class DashboardService:
    """Read-only localhost dashboard for bridge observability.

    This server intentionally runs on a separate localhost-only port so it is not
    exposed through the MCP Quick Tunnel. It provides GET-only status APIs and a
    static dashboard; it cannot mutate files, run commands, or change permissions.
    """

    def __init__(
        self,
        settings: Settings,
        activity: ActivityService,
        tasks: TaskService,
        terminal: TerminalService,
        vscode: VSCodeService,
        launcher_state: LauncherStateStore | None = None,
    ):
        self.settings = settings
        self.activity = activity
        self.tasks = tasks
        self.terminal = terminal
        self.vscode = vscode
        self.launcher_state = launcher_state or LauncherStateStore()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int | None = None

    def snapshot(self) -> dict[str, Any]:
        task_state = self.tasks.get_state()
        progress = self.tasks.get_progress_events(limit=100)
        activity = self.activity.list_events(limit=200)
        commands = self.terminal.list_commands(limit=20, tail_bytes=4096)
        vscode = self.vscode.health()
        launcher = self.launcher_state.read()
        todos = task_state.get("todos", []) if isinstance(task_state.get("todos"), list) else []
        counts = {
            "pending": sum(1 for item in todos if isinstance(item, dict) and item.get("status") == "pending"),
            "in_progress": sum(1 for item in todos if isinstance(item, dict) and item.get("status") == "in_progress"),
            "completed": sum(1 for item in todos if isinstance(item, dict) and item.get("status") == "completed"),
        }
        return {
            "system": {
                "workspace_root": str(self.settings.workspace_root),
                "allow_write": self.settings.allow_write,
                "allow_commands": self.settings.allow_commands,
                "bridge_host": self.settings.bridge_host,
                "bridge_port": self.settings.bridge_port,
                "dashboard_host": "127.0.0.1",
                "dashboard_port": self.port,
            },
            "task": task_state,
            "todo_counts": counts,
            "progress": progress,
            "activity": activity,
            "commands": commands,
            "vscode": vscode,
            "launcher": launcher,
        }

    def _handler_class(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "LocalAIBridgeDashboard/0.2"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _headers(self, status: int, content_type: str, length: int) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
                )
                self.end_headers()

            def _json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self._headers(status, "application/json; charset=utf-8", len(body))
                self.wfile.write(body)

            def _html(self) -> None:
                try:
                    body = _DASHBOARD_HTML.read_bytes()
                except OSError:
                    self._json(500, {"error": "DASHBOARD_HTML_MISSING"})
                    return
                self._headers(200, "text/html; charset=utf-8", len(body))
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
                parsed = urlparse(self.path)
                if parsed.path in {"/", "/index.html"}:
                    self._html()
                    return
                if parsed.path == "/api/state":
                    self._json(200, service.snapshot())
                    return
                if parsed.path == "/api/activity":
                    query = parse_qs(parsed.query)
                    try:
                        after_seq = int(query.get("after_seq", ["0"])[0])
                        limit = int(query.get("limit", ["200"])[0])
                    except ValueError:
                        self._json(400, {"error": "INVALID_QUERY"})
                        return
                    self._json(200, service.activity.list_events(limit=limit, after_seq=after_seq))
                    return
                if parsed.path == "/api/progress":
                    query = parse_qs(parsed.query)
                    try:
                        after_seq = int(query.get("after_seq", ["0"])[0])
                        limit = int(query.get("limit", ["100"])[0])
                    except ValueError:
                        self._json(400, {"error": "INVALID_QUERY"})
                        return
                    self._json(200, service.tasks.get_progress_events(limit=limit, after_seq=after_seq))
                    return
                if parsed.path == "/api/launcher":
                    self._json(200, service.launcher_state.read())
                    return
                self._json(404, {"error": "not_found"})

        return Handler

    def start(self) -> dict[str, Any]:
        if not self.settings.dashboard_enabled:
            return {"enabled": False, "url": None, "port": None}
        if self._server is not None:
            return {"enabled": True, "url": f"http://127.0.0.1:{self.port}/", "port": self.port}
        last_error: OSError | None = None
        for port in range(self.settings.dashboard_port, min(65_535, self.settings.dashboard_port + 10) + 1):
            try:
                server = ThreadingHTTPServer(("127.0.0.1", port), self._handler_class())
            except OSError as exc:
                last_error = exc
                continue
            self._server = server
            self.port = int(server.server_address[1])
            self._thread = threading.Thread(
                target=server.serve_forever,
                daemon=True,
                name="local-ai-bridge-dashboard",
            )
            self._thread.start()
            self.activity.emit(
                "system_state",
                status="ready",
                title="Observability dashboard started",
                component="dashboard",
                details={"url": f"http://127.0.0.1:{self.port}/"},
            )
            return {"enabled": True, "url": f"http://127.0.0.1:{self.port}/", "port": self.port}
        if last_error is not None:
            raise last_error
        raise RuntimeError("NO_AVAILABLE_DASHBOARD_PORT")

    def stop(self) -> None:
        server = self._server
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._server = None
        self._thread = None
        self.activity.emit(
            "system_state",
            status="stopped",
            title="Observability dashboard stopped",
            component="dashboard",
        )
