from __future__ import annotations

import json
import secrets
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .activity_service import ActivityService
from .ai_telemetry import AITelemetryService
from .approval_service import ApprovalService
from .config import Settings
from .launcher_state import LauncherStateStore
from .jsonl_utils import JsonlRotationPolicy
from .task_service import TaskService
from .workspace_registry import WorkspaceRegistry


_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_HTML = _PACKAGE_ROOT / "dashboard" / "index.html"
_ADVANCED_DASHBOARD_HTML = _PACKAGE_ROOT / "dashboard" / "advanced.html"


class DashboardService:
    """Localhost observability/control plane with multi-workspace awareness."""

    def __init__(
        self,
        settings: Settings,
        activity: ActivityService,
        tasks: TaskService,
        registry: WorkspaceRegistry,
        launcher_state: LauncherStateStore | None = None,
        approvals: ApprovalService | None = None,
        ai_telemetry: AITelemetryService | None = None,
    ):
        self.settings = settings
        self.activity = activity
        self.tasks = tasks
        self.registry = registry
        self.launcher_state = launcher_state or LauncherStateStore()
        self.approvals = approvals or ApprovalService(ttl_seconds=settings.approval_ttl_seconds)
        self.ai_telemetry = ai_telemetry or AITelemetryService()
        self.telemetry_token = secrets.token_urlsafe(32)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int | None = None

    def _git_worktree_changes(self) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for workspace in self.registry.list_workspaces().get("workspaces", []):
            if not isinstance(workspace, dict):
                continue
            workspace_id = str(workspace.get("workspace_id") or "default")
            root = Path(str(workspace.get("workspace_root") or ""))
            if not root.is_dir():
                continue

            repo_roots: list[Path] = []
            if (root / ".git").exists():
                repo_roots.append(root)
            else:
                try:
                    for child in root.iterdir():
                        if len(repo_roots) >= 16:
                            break
                        if child.is_dir() and (child / ".git").exists():
                            repo_roots.append(child)
                except OSError:
                    pass

            for repo_root in repo_roots:
                try:
                    result = subprocess.run(
                        ["git", "-c", "core.quotepath=false", "status", "--short", "--untracked-files=all"],
                        cwd=repo_root,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=2.0,
                    )
                except (OSError, subprocess.SubprocessError):
                    continue
                if result.returncode != 0:
                    continue
                try:
                    repo_prefix = repo_root.resolve().relative_to(root.resolve())
                except ValueError:
                    repo_prefix = Path()
                for raw_line in result.stdout.splitlines():
                    if len(raw_line) < 4:
                        continue
                    code = raw_line[:2]
                    repo_relative = raw_line[3:].strip()
                    if " -> " in repo_relative:
                        repo_relative = repo_relative.rsplit(" -> ", 1)[1].strip()
                    repo_relative = repo_relative.strip('"')
                    if not repo_relative:
                        continue
                    if code == "??" or "A" in code:
                        action = "add"
                    elif "D" in code:
                        action = "delete"
                    else:
                        action = "modify"
                    workspace_relative = (repo_prefix / Path(repo_relative)).as_posix()
                    key = (workspace_id, workspace_relative)
                    if key in seen:
                        continue
                    seen.add(key)
                    changes.append({
                        "timestamp": None,
                        "workspace_id": workspace_id,
                        "action": action,
                        "action_label": {"add": "Added", "modify": "Modified", "delete": "Deleted"}[action],
                        "path": workspace_relative,
                        "absolute_path": str((repo_root / repo_relative).resolve()),
                        "source": "git_status",
                    })
        return changes

    def file_changes(self, limit: int = 80) -> dict[str, Any]:
        limit = max(1, min(int(limit), 300))
        current = self._git_worktree_changes()
        payload = self.activity.list_events(limit=1200)
        events = payload.get("events", []) if isinstance(payload, dict) else []
        changes: list[dict[str, Any]] = list(current)
        seen = {(item.get("workspace_id"), item.get("path"), item.get("action")) for item in current}
        action_names = {
            "add": "Added",
            "create": "Added",
            "update": "Modified",
            "modify": "Modified",
            "write": "Modified",
            "delete": "Deleted",
            "remove": "Deleted",
        }
        for event in reversed(events):
            if not isinstance(event, dict) or event.get("type") != "file_changed":
                continue
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            workspace_id = str(details.get("workspace_id") or "default")
            raw_files = details.get("files")
            if isinstance(raw_files, list):
                file_items = [item for item in raw_files if isinstance(item, dict)]
            else:
                file_items = [{
                    "path": details.get("path"),
                    "absolute_path": details.get("absolute_path"),
                    "action": details.get("action") or "modify",
                }]
            for item in file_items:
                relative = str(item.get("path") or "").strip()
                if not relative:
                    continue
                absolute = item.get("absolute_path")
                if not absolute:
                    try:
                        absolute = str(self.registry.get(workspace_id).guard.resolve(relative))
                    except Exception:
                        absolute = relative
                action = str(item.get("action") or "modify").casefold()
                key = (workspace_id, relative, action)
                if key in seen:
                    continue
                seen.add(key)
                changes.append({
                    "timestamp": event.get("timestamp"),
                    "workspace_id": workspace_id,
                    "action": action,
                    "action_label": action_names.get(action, action),
                    "path": relative,
                    "absolute_path": str(absolute),
                    "source": "activity",
                })
                if len(changes) >= limit:
                    return {"changes": changes[:limit], "count": min(len(changes), limit)}
        return {"changes": changes[:limit], "count": min(len(changes), limit)}

    def focus_snapshot(self) -> dict[str, Any]:
        return {
            "ai_output": self.ai_telemetry.snapshot(),
            "task": self.tasks.get_state(),
            "file_changes": self.file_changes(limit=100),
        }

    def snapshot(self) -> dict[str, Any]:
        task_state = self.tasks.get_state()
        progress = self.tasks.get_progress_events(limit=100)
        activity = self.activity.list_events(limit=200)
        commands = self.registry.list_commands(limit=40, tail_bytes=4096)
        vscode = self.registry.vscode_health()
        workspaces = self.registry.list_workspaces(include_vscode=False)
        launcher = self.launcher_state.read()
        approval_state = self.approvals.list_requests(include_terminal=True, limit=100)
        rotation = JsonlRotationPolicy.from_env()
        todos = task_state.get("todos", []) if isinstance(task_state.get("todos"), list) else []
        counts = {
            "pending": sum(1 for item in todos if isinstance(item, dict) and item.get("status") == "pending"),
            "in_progress": sum(1 for item in todos if isinstance(item, dict) and item.get("status") == "in_progress"),
            "completed": sum(1 for item in todos if isinstance(item, dict) and item.get("status") == "completed"),
        }
        return {
            "system": {
                "workspace_root": str(self.settings.workspace_root),
                "default_workspace_id": "default",
                "workspace_count": workspaces.get("count", 1),
                "allow_write": self.settings.allow_write,
                "allow_commands": self.settings.allow_commands,
                "bridge_host": self.settings.bridge_host,
                "bridge_port": self.settings.bridge_port,
                "dashboard_host": "127.0.0.1",
                "dashboard_port": self.port,
                "log_rotation_max_bytes": rotation.max_bytes,
                "log_rotation_backups": rotation.backup_count,
                "confirm_writes": self.settings.confirm_writes,
                "confirm_commands": self.settings.confirm_commands,
                "approval_ttl_seconds": self.settings.approval_ttl_seconds,
            },
            "workspaces": workspaces,
            "task": task_state,
            "todo_counts": counts,
            "progress": progress,
            "activity": activity,
            "commands": commands,
            "vscode": vscode,
            "launcher": launcher,
            "approvals": approval_state,
            "ai_output": self.ai_telemetry.snapshot(),
            "file_changes": self.file_changes(limit=100),
        }

    def _handler_class(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "LocalAIBridgeDashboard/0.4"

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

            def _html(self, path: Path = _DASHBOARD_HTML) -> None:
                try:
                    body = path.read_bytes()
                except OSError:
                    self._json(500, {"error": "DASHBOARD_HTML_MISSING"})
                    return
                self._headers(200, "text/html; charset=utf-8", len(body))
                self.wfile.write(body)

            def _local_host_ok(self) -> bool:
                raw = self.headers.get("Host", "")
                host = raw.rsplit(":", 1)[0].strip("[]").casefold() if ":" in raw else raw.casefold()
                return host in {"127.0.0.1", "localhost"}

            def _mutation_authorized(self) -> bool:
                if not self._local_host_ok():
                    return False
                candidate = self.headers.get("X-Bridge-Dashboard-Token", "")
                if not candidate or not secrets.compare_digest(candidate, service.approvals.dashboard_token):
                    return False
                origin = self.headers.get("Origin")
                if origin:
                    expected = "http://" + self.headers.get("Host", "")
                    if origin.rstrip("/").casefold() != expected.rstrip("/").casefold():
                        return False
                return True

            def _telemetry_authorized(self) -> bool:
                # Browser extensions use extension:// origins, so telemetry gets
                # a separate ephemeral localhost-only token instead of the
                # stricter same-origin Dashboard mutation policy.
                if not self._local_host_ok():
                    return False
                candidate = self.headers.get("X-Bridge-Telemetry-Token", "")
                return bool(candidate) and secrets.compare_digest(candidate, service.telemetry_token)

            def _read_json_body(self) -> dict[str, Any]:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError as exc:
                    raise ValueError("INVALID_CONTENT_LENGTH") from exc
                if length < 0 or length > 4096:
                    raise ValueError("REQUEST_BODY_TOO_LARGE")
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("INVALID_JSON") from exc
                if not isinstance(payload, dict):
                    raise ValueError("JSON_OBJECT_REQUIRED")
                return payload

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path in {"/", "/index.html"}:
                    self._html()
                    return
                if parsed.path == "/advanced.html":
                    self._html(_ADVANCED_DASHBOARD_HTML)
                    return
                if parsed.path == "/api/focus":
                    self._json(200, service.focus_snapshot())
                    return
                if parsed.path == "/api/ai-output":
                    self._json(200, service.ai_telemetry.snapshot())
                    return
                if parsed.path == "/api/file-changes":
                    self._json(200, service.file_changes(limit=100))
                    return
                if parsed.path == "/api/state":
                    self._json(200, service.snapshot())
                    return
                if parsed.path == "/api/workspaces":
                    self._json(200, service.registry.list_workspaces(include_vscode=True))
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
                if parsed.path == "/api/approvals":
                    self._json(200, service.approvals.list_requests(include_terminal=True, limit=100))
                    return
                if parsed.path == "/api/session":
                    self._json(200, {
                        "dashboard_token": service.approvals.dashboard_token,
                        "telemetry_token": service.telemetry_token,
                        "confirm_writes": service.settings.confirm_writes,
                        "confirm_commands": service.settings.confirm_commands,
                        "approval_ttl_seconds": service.settings.approval_ttl_seconds,
                    })
                    return
                self._json(404, {"error": "not_found"})

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/api/telemetry/ai-output":
                    if not self._telemetry_authorized():
                        self._json(403, {"error": "telemetry_forbidden"})
                        return
                    try:
                        payload = self._read_json_body()
                        result = service.ai_telemetry.record(
                            output_tokens=int(payload.get("output_tokens")),
                            duration_ms=int(payload.get("duration_ms")),
                            source=str(payload.get("source") or "client"),
                            model=str(payload.get("model")) if payload.get("model") else None,
                        )
                    except (TypeError, ValueError) as exc:
                        self._json(400, {"error": str(exc)})
                        return
                    self._json(200, result)
                    return
                if not self._mutation_authorized():
                    self._json(403, {"error": "dashboard_mutation_forbidden"})
                    return
                parts = [part for part in parsed.path.split("/") if part]
                if len(parts) != 3 or parts[:2] != ["api", "approvals"]:
                    self._json(404, {"error": "not_found"})
                    return
                request_id = parts[2]
                try:
                    payload = self._read_json_body()
                    decision = str(payload.get("decision", ""))
                    result = service.approvals.decide(request_id, decision)
                except KeyError as exc:
                    self._json(404, {"error": str(exc.args[0] if exc.args else "APPROVAL_REQUEST_NOT_FOUND")})
                    return
                except (ValueError, RuntimeError) as exc:
                    self._json(409, {"error": str(exc)})
                    return
                service.activity.emit(
                    "approval_decided",
                    status=result.get("status", "completed"),
                    title=f"Approval {result.get('status')}: {result.get('title')}",
                    component="approvals",
                    details={"request_id": request_id, "action": result.get("action")},
                )
                self._json(200, result)

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