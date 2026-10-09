from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from typing import Any, TypeVar

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from bridge.activity_service import ActivityService, redact_text
from bridge.approval_service import ApprovalService
from bridge.config import Settings
from bridge.dashboard_service import DashboardService
from bridge.file_service import FileService
from bridge.patch_service import PatchService
from bridge.pathguard import WorkspaceGuard
from bridge.security import build_secure_mcp_app
from bridge.task_service import TaskService
from bridge.terminal_service import TerminalService
from bridge.vscode_service import VSCodeService


T = TypeVar("T")

settings = Settings.from_env()
guard = WorkspaceGuard(settings.workspace_root)
activity = ActivityService()
approvals = ApprovalService(ttl_seconds=settings.approval_ttl_seconds)
files = FileService(settings, guard)
patches = PatchService(settings, guard)
tasks = TaskService()
terminal = TerminalService(settings, guard, activity=activity)
vscode_service = VSCodeService(settings, guard)
dashboard = DashboardService(settings, activity, tasks, terminal, vscode_service, approvals=approvals)

# FastMCP auto-enables DNS rebinding protection when bound to localhost, but its
# default allowlist only contains 127.0.0.1/localhost/[::1]. A Quick Tunnel reaches
# us with the public tunnel hostname in the Host header, which would be rejected
# with HTTP 421 Misdirected Request. BRIDGE_ALLOWED_HOSTS adds the tunnel host to
# the allowlist while keeping the localhost protection intact. When unset we pass
# None so the library behaviour is unchanged.
if settings.allowed_hosts or settings.allowed_origins:
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", *settings.allowed_hosts],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*", *settings.allowed_origins],
    )
else:
    transport_security = None

mcp = FastMCP("Local AI Development Bridge", transport_security=transport_security)


def _observed(
    tool: str,
    title: str,
    callback: Callable[[], T],
    *,
    details: dict[str, Any] | None = None,
    summarize: Callable[[T], dict[str, Any] | None] | None = None,
) -> T:
    activity_id = activity.start(tool, title, details)
    try:
        result = callback()
    except Exception as exc:
        activity.finish(
            activity_id,
            tool=tool,
            title=f"{title} failed",
            status="failed",
            details={"error_type": type(exc).__name__, "message": redact_text(str(exc), 600)},
        )
        raise
    summary = summarize(result) if summarize is not None else None
    activity.finish(
        activity_id,
        tool=tool,
        title=f"{title} completed",
        status="completed",
        details=summary,
    )
    return result


def _approval_gate(
    enabled: bool,
    approval_id: str | None,
    action: str,
    payload: dict[str, Any],
    *,
    title: str,
    details: dict[str, Any],
) -> dict[str, Any] | None:
    if not enabled:
        return None
    try:
        pending = approvals.require_or_request(
            approval_id,
            action,
            payload,
            title=title,
            details=details,
        )
    except Exception as exc:
        activity.emit(
            "approval_failed",
            status="failed",
            title=f"Approval check failed: {title}",
            component="approvals",
            details={"action": action, "error": redact_text(str(exc), 400)},
        )
        raise
    if pending is not None:
        request = pending.get("approval", {})
        activity.emit(
            "approval_requested",
            status="pending",
            title=title,
            component="approvals",
            details={
                "request_id": request.get("request_id"),
                "action": action,
                "expires_epoch": request.get("expires_epoch"),
                **details,
            },
        )
        return pending
    activity.emit(
        "approval_consumed",
        status="completed",
        title=f"Approved: {title}",
        component="approvals",
        details={"request_id": approval_id, "action": action, **details},
    )
    return None


@mcp.tool()
def workspace_info() -> dict[str, Any]:
    """Return workspace root, active permissions and bounded-output limits."""
    return files.workspace_info()


@mcp.tool()
def list_directory(
    path: str = ".",
    cursor: str | None = None,
    limit: int = 100,
    include_hidden: bool = False,
    include_ignored: bool = False,
) -> dict[str, Any]:
    """List one directory with stable name-based pagination."""
    return _observed(
        "list_directory",
        f"List directory: {path}",
        lambda: files.list_directory(path, cursor, limit, include_hidden, include_ignored),
        details={"path": path, "limit": limit},
        summarize=lambda result: {"entries": len(result.get("entries", [])), "truncated": result.get("truncated", False)},
    )


@mcp.tool()
def find_files(
    patterns: list[str],
    path: str = ".",
    exclude: list[str] | None = None,
    include_hidden: bool = False,
    include_ignored: bool = False,
    sort: str = "path_asc",
    max_results: int = 100,
) -> dict[str, Any]:
    """Find workspace files by glob patterns with bounded results."""
    return _observed(
        "find_files",
        f"Find files in: {path}",
        lambda: files.find_files(patterns, path, exclude, include_hidden, include_ignored, sort, max_results),
        details={"path": path, "patterns": patterns, "max_results": max_results},
        summarize=lambda result: {"matches": len(result.get("matches", [])), "truncated": result.get("truncated", False)},
    )


@mcp.tool()
def read_files(requests: list[dict[str, Any]]) -> dict[str, Any]:
    """Read 1..20 UTF-8 files/ranges and return raw-byte SHA-256 versions."""
    paths = [str(item.get("path", "")) for item in requests[:20]]
    return _observed(
        "read_files",
        f"Read {len(requests)} file(s)",
        lambda: files.read_files(requests),
        details={"paths": paths},
        summarize=lambda result: {
            "files": len(result.get("files", [])),
            "content_bytes": result.get("total_content_bytes", 0),
        },
    )


@mcp.tool()
def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> dict[str, Any]:
    """Compatibility single-file reader with SHA-256 version metadata."""
    return _observed(
        "read_file",
        f"Read file: {path}",
        lambda: files.read_file(path, start_line, end_line),
        details={"path": path, "start_line": start_line, "end_line": end_line},
        summarize=lambda result: {
            "path": result.get("path", path),
            "size_bytes": result.get("size_bytes"),
            "truncated": result.get("truncated", False),
        },
    )


@mcp.tool()
def search_files(
    pattern: str,
    path: str = ".",
    glob: list[str] | None = None,
    regex: bool = False,
    case_sensitive: bool = True,
    context_lines: int = 1,
    max_results: int = 100,
    max_per_file: int = 20,
    include_hidden: bool = False,
    include_ignored: bool = False,
) -> dict[str, Any]:
    """Search UTF-8 text by literal text or regex with bounded context and results."""
    return _observed(
        "search_files",
        f"Search files in: {path}",
        lambda: files.search_files(
            pattern,
            path,
            glob,
            regex,
            case_sensitive,
            context_lines,
            max_results,
            max_per_file,
            include_hidden,
            include_ignored,
        ),
        details={"path": path, "pattern": redact_text(pattern, 160), "regex": regex},
        summarize=lambda result: {
            "results": len(result.get("results", [])),
            "scanned_files": result.get("scanned_files", 0),
            "truncated": result.get("truncated", False),
        },
    )


@mcp.tool()
def write_file(path: str, content: str, expected_version: str | None = None, approval_id: str | None = None) -> dict[str, Any]:
    """Create/overwrite UTF-8 text; when configured, requires one-time local Dashboard approval."""
    content_bytes = content.encode("utf-8")
    gate = _approval_gate(
        settings.confirm_writes,
        approval_id,
        "write_file",
        {
            "path": path,
            "content_sha256": hashlib.sha256(content_bytes).hexdigest(),
            "expected_version": expected_version,
        },
        title=f"Approve file write: {path}",
        details={"path": path, "bytes_requested": len(content_bytes)},
    )
    if gate is not None:
        return gate
    result = _observed(
        "write_file",
        f"Write file: {path}",
        lambda: files.write_file(path, content, expected_version),
        details={"path": path, "bytes_requested": len(content.encode("utf-8"))},
        summarize=lambda value: {"path": value.get("path"), "bytes": value.get("bytes")},
    )
    activity.emit(
        "file_changed",
        status="completed",
        title=f"File written: {result.get('path', path)}",
        component="files",
        details={"path": result.get("path", path), "bytes": result.get("bytes")},
    )
    return result


@mcp.tool()
def apply_patch(patch: str, expected_versions: dict[str, str | None], approval_id: str | None = None) -> dict[str, Any]:
    """Apply a validated multi-file unified diff; optionally requires local approval."""
    gate = _approval_gate(
        settings.confirm_writes,
        approval_id,
        "apply_patch",
        {
            "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
            "expected_versions": expected_versions,
        },
        title="Approve multi-file patch",
        details={"files": sorted(expected_versions.keys()), "patch_bytes": len(patch.encode("utf-8"))},
    )
    if gate is not None:
        return gate
    result = _observed(
        "apply_patch",
        "Apply multi-file patch",
        lambda: patches.apply_patch(patch, expected_versions),
        details={"expected_files": sorted(expected_versions.keys())},
        summarize=lambda value: {
            "applied": value.get("applied", False),
            "files": [{"path": item.get("path"), "action": item.get("action")} for item in value.get("files", [])],
        },
    )
    activity.emit(
        "file_changed",
        status="completed",
        title="Patch changed files",
        component="files",
        details={"files": [{"path": item.get("path"), "action": item.get("action")} for item in result.get("files", [])]},
    )
    return result


@mcp.tool()
def run_command(
    command: str,
    cwd: str = ".",
    background: bool = False,
    timeout_ms: int = 120_000,
    approval_id: str | None = None,
) -> dict[str, Any]:
    """Run a command in a persistent native PTY; optionally requires local approval."""
    gate = _approval_gate(
        settings.confirm_commands,
        approval_id,
        "run_command",
        {"command": command, "cwd": cwd, "background": background, "timeout_ms": timeout_ms},
        title="Approve terminal command",
        details={"cwd": cwd, "background": background, "command_preview": redact_text(command, 240)},
    )
    if gate is not None:
        return gate
    return _observed(
        "run_command",
        "Run terminal command",
        lambda: terminal.run_command(command, cwd, background, timeout_ms),
        details={"cwd": cwd, "background": background, "command_preview": redact_text(command, 240)},
        summarize=lambda result: {
            "command_id": result.get("command_id"),
            "status": result.get("status"),
            "exit_code": result.get("exit_code"),
        },
    )


@mcp.tool()
def get_command_output(command_id: str, offset: int = 0, max_bytes: int = 32_768) -> dict[str, Any]:
    """Read incremental terminal output using absolute UTF-8 byte offsets."""
    return terminal.get_command_output(command_id, offset, max_bytes)


@mcp.tool()
def send_command_input(command_id: str, input: str, append_newline: bool = True) -> dict[str, Any]:
    """Send interactive UTF-8 input to a running PTY command."""
    return _observed(
        "send_command_input",
        "Send terminal input",
        lambda: terminal.send_command_input(command_id, input, append_newline),
        details={"command_id": command_id, "append_newline": append_newline, "input_bytes": len(input.encode("utf-8"))},
        summarize=lambda result: {"command_id": result.get("command_id"), "input_seq": result.get("input_seq")},
    )


@mcp.tool()
def wait(command_id: str, timeout_ms: int = 30_000) -> dict[str, Any]:
    """Wait for terminal completion or new output without terminating the underlying process."""
    return terminal.wait(command_id, timeout_ms)


@mcp.tool()
def terminate_command(command_id: str, force: bool = False) -> dict[str, Any]:
    """Terminate a running PTY command."""
    return _observed(
        "terminate_command",
        "Terminate terminal command",
        lambda: terminal.terminate(command_id, force),
        details={"command_id": command_id, "force": force},
        summarize=lambda result: result,
    )


@mcp.tool()
def set_todos(todos: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace the durable task snapshot; at most one todo may be in_progress."""
    result = _observed(
        "set_todos",
        "Update task plan",
        lambda: tasks.set_todos(todos),
        details={"todo_count": len(todos)},
        summarize=lambda value: {
            "version": value.get("version"),
            "todo_count": len(value.get("todos", [])),
            "active": next((item.get("id") for item in value.get("todos", []) if item.get("status") == "in_progress"), None),
        },
    )
    activity.emit(
        "task_updated",
        status="completed",
        title="Task plan updated",
        component="tasks",
        details={"version": result.get("version"), "todos": result.get("todos", [])},
    )
    return result


@mcp.tool()
def report_progress(
    message: str,
    todo_id: str | None = None,
    current: int | None = None,
    total: int | None = None,
    phase: int | None = None,
    phase_total: int | None = None,
) -> dict[str, Any]:
    """Append one durable progress event, optionally associated with the active todo."""
    result = tasks.report_progress(message, todo_id, current, total, phase, phase_total)
    activity.emit(
        "progress_reported",
        status="completed",
        title=message,
        component="tasks",
        details={
            "todo_id": result.get("todo_id"),
            "current": result.get("current"),
            "total": result.get("total"),
            "phase": result.get("phase"),
            "phase_total": result.get("phase_total"),
        },
    )
    return result


@mcp.tool()
def get_task_state() -> dict[str, Any]:
    """Return the current durable todo snapshot and progress event count."""
    return tasks.get_state()


@mcp.tool()
def get_progress_events(limit: int = 100, after_seq: int | None = None) -> dict[str, Any]:
    """Return concrete durable progress events for visual progress tracking."""
    return tasks.get_progress_events(limit, after_seq)


@mcp.tool()
def get_activity(limit: int = 100, after_seq: int | None = None) -> dict[str, Any]:
    """Return recent structured bridge activity events with sensitive values redacted."""
    return activity.list_events(limit, after_seq)


@mcp.tool()
def dashboard_info() -> dict[str, Any]:
    """Return local observability dashboard availability and address."""
    return {
        "enabled": settings.dashboard_enabled,
        "host": "127.0.0.1",
        "port": dashboard.port,
        "url": f"http://127.0.0.1:{dashboard.port}/" if dashboard.port is not None else None,
        "read_only": False,
        "actions": "approval_decisions_only",
        "tunnel_exposed": False,
        "confirm_writes": settings.confirm_writes,
        "confirm_commands": settings.confirm_commands,
    }


@mcp.tool()
def vscode_health() -> dict[str, Any]:
    """Return VS Code Companion readiness, workspace roots and open-document state."""
    return _observed(
        "vscode_health",
        "Check VS Code Companion",
        vscode_service.health,
        summarize=lambda result: {"provider_state": result.get("provider_state"), "reason": result.get("provider_state_reason")},
    )


@mcp.tool()
def get_diagnostics(
    path: str | None = None,
    severity: list[str] | None = None,
    max_results: int = 100,
) -> dict[str, Any]:
    """Read live VS Code diagnostics, including dirty editor state when available."""
    return _observed(
        "get_diagnostics",
        f"Read VS Code diagnostics{': ' + path if path else ''}",
        lambda: vscode_service.get_diagnostics(path, severity, max_results),
        details={"path": path, "severity": severity, "max_results": max_results},
        summarize=lambda result: {
            "provider_state": result.get("provider_state"),
            "results": len(result.get("results", [])),
            "truncated": result.get("truncated", False),
        },
    )


@mcp.tool()
def lsp(
    operation: str,
    path: str | None = None,
    line: int | None = None,
    column: int | None = None,
    query: str | None = None,
    include_declaration: bool = True,
    max_results: int = 100,
) -> dict[str, Any]:
    """Query VS Code language providers for symbols, definitions, references, implementations or hover data."""
    return _observed(
        "lsp",
        f"VS Code LSP: {operation}",
        lambda: vscode_service.lsp(
            operation,
            path,
            line,
            column,
            query,
            include_declaration,
            max_results,
        ),
        details={"operation": operation, "path": path, "line": line, "column": column, "query": query},
        summarize=lambda result: {
            "provider_state": result.get("provider_state"),
            "semantic_state": result.get("semantic_state"),
            "semantic_result_inconclusive": result.get("semantic_result_inconclusive"),
            "document_state": result.get("document_state"),
            "language_id": result.get("language_id"),
            "warmup_retry_attempted": result.get("warmup_retry_attempted"),
            "operation": result.get("operation", operation),
            "results": len(result.get("results", [])),
            "truncated": result.get("truncated", False),
        },
    )


@mcp.tool()
def read_editor_buffer(
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
    max_bytes: int = 262_144,
) -> dict[str, Any]:
    """Read the current VS Code text buffer, including unsaved edits, with bounded output."""
    return _observed(
        "read_editor_buffer",
        f"Read editor buffer: {path}",
        lambda: vscode_service.read_editor_buffer(path, start_line, end_line, max_bytes),
        details={"path": path, "start_line": start_line, "end_line": end_line},
        summarize=lambda result: {
            "provider_state": result.get("provider_state"),
            "document_dirty": result.get("document_dirty"),
            "truncated": result.get("truncated", False),
        },
    )


if __name__ == "__main__":
    app, token = build_secure_mcp_app(mcp)
    host = settings.bridge_host
    port = settings.bridge_port
    dashboard_state = dashboard.start()
    activity.emit(
        "system_state",
        status="ready",
        title="MCP Bridge starting",
        component="bridge",
        details={
            "workspace_root": str(settings.workspace_root),
            "allow_write": settings.allow_write,
            "allow_commands": settings.allow_commands,
            "bridge_host": host,
            "bridge_port": port,
            "confirm_writes": settings.confirm_writes,
            "confirm_commands": settings.confirm_commands,
        },
    )
    print(f"Local MCP endpoint: http://{host}:{port}/{token}/mcp")
    if dashboard_state.get("enabled"):
        print(f"Observability dashboard: {dashboard_state['url']} (localhost-only, read-only)")
    if settings.allowed_hosts:
        print(f"Extra allowed Host headers: {', '.join(settings.allowed_hosts)}")
    print("The token is a capability secret. Do not publish or commit it.")
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        terminal_state = terminal.shutdown(timeout=3.0)
        activity.emit(
            "system_state",
            status="stopped",
            title="Terminal service stopped",
            component="terminal",
            details=terminal_state,
        )
        dashboard.stop()
