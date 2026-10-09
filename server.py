from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any, TypeVar

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from bridge.activity_service import ActivityService, redact_text
from bridge.approval_service import ApprovalService
from bridge.config import Settings
from bridge.dashboard_service import DashboardService
from bridge.security import build_secure_mcp_app
from bridge.task_service import TaskService
from bridge.workspace_registry import WorkspaceRegistry


T = TypeVar("T")

settings = Settings.from_env()
activity = ActivityService()
approvals = ApprovalService(ttl_seconds=settings.approval_ttl_seconds)
registry = WorkspaceRegistry.from_env(settings, activity=activity)
tasks = TaskService()
dashboard = DashboardService(settings, activity, tasks, registry, approvals=approvals)

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
    workspace_id: str | None = None,
) -> T:
    event_details = dict(details or {})
    if workspace_id is not None:
        event_details["workspace_id"] = workspace_id
    activity_id = activity.start(tool, title, event_details or None)
    try:
        result = callback()
    except Exception as exc:
        failed_details: dict[str, Any] = {
            "error_type": type(exc).__name__,
            "message": redact_text(str(exc), 600),
        }
        if workspace_id is not None:
            failed_details["workspace_id"] = workspace_id
        activity.finish(
            activity_id,
            tool=tool,
            title=f"{title} failed",
            status="failed",
            details=failed_details,
        )
        raise
    summary = summarize(result) if summarize is not None else None
    if workspace_id is not None:
        summary = dict(summary or {})
        summary["workspace_id"] = workspace_id
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
            details={"action": action, "error": redact_text(str(exc), 400), **details},
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
def list_workspaces(include_vscode: bool = False) -> dict[str, Any]:
    """List configured workspace IDs and roots. `default` preserves legacy behavior."""
    return registry.list_workspaces(include_vscode=include_vscode)


@mcp.tool()
def workspace_info(workspace_id: str | None = None) -> dict[str, Any]:
    """Return workspace root, permissions and limits for one workspace."""
    return registry.workspace_info(workspace_id)


@mcp.tool()
def list_directory(
    path: str = ".",
    cursor: str | None = None,
    limit: int = 100,
    include_hidden: bool = False,
    include_ignored: bool = False,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """List one directory in the selected workspace with stable pagination."""
    context = registry.get(workspace_id)
    return _observed(
        "list_directory",
        f"List directory: {path}",
        lambda: context.files.list_directory(path, cursor, limit, include_hidden, include_ignored),
        details={"path": path, "limit": limit},
        summarize=lambda result: {"entries": len(result.get("entries", [])), "truncated": result.get("truncated", False)},
        workspace_id=context.workspace_id,
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
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Find files inside the selected workspace."""
    context = registry.get(workspace_id)
    return _observed(
        "find_files",
        f"Find files in: {path}",
        lambda: context.files.find_files(patterns, path, exclude, include_hidden, include_ignored, sort, max_results),
        details={"path": path, "patterns": patterns, "max_results": max_results},
        summarize=lambda result: {"matches": len(result.get("matches", [])), "truncated": result.get("truncated", False)},
        workspace_id=context.workspace_id,
    )


@mcp.tool()
def read_files(requests: list[dict[str, Any]], workspace_id: str | None = None) -> dict[str, Any]:
    """Read 1..20 UTF-8 files/ranges from the selected workspace."""
    context = registry.get(workspace_id)
    paths = [str(item.get("path", "")) for item in requests[:20]]
    return _observed(
        "read_files",
        f"Read {len(requests)} file(s)",
        lambda: context.files.read_files(requests),
        details={"paths": paths},
        summarize=lambda result: {
            "files": len(result.get("files", [])),
            "content_bytes": result.get("total_content_bytes", 0),
        },
        workspace_id=context.workspace_id,
    )


@mcp.tool()
def read_file(
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Read one file from the selected workspace."""
    context = registry.get(workspace_id)
    return _observed(
        "read_file",
        f"Read file: {path}",
        lambda: context.files.read_file(path, start_line, end_line),
        details={"path": path, "start_line": start_line, "end_line": end_line},
        summarize=lambda result: {
            "path": result.get("path", path),
            "size_bytes": result.get("size_bytes"),
            "truncated": result.get("truncated", False),
        },
        workspace_id=context.workspace_id,
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
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Search UTF-8 text inside the selected workspace."""
    context = registry.get(workspace_id)
    return _observed(
        "search_files",
        f"Search files in: {path}",
        lambda: context.files.search_files(
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
        workspace_id=context.workspace_id,
    )


@mcp.tool()
def write_file(
    path: str,
    content: str,
    expected_version: str | None = None,
    approval_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Create/overwrite UTF-8 text in one workspace; optionally requires approval."""
    context = registry.get(workspace_id)
    content_bytes = content.encode("utf-8")
    gate = _approval_gate(
        settings.confirm_writes,
        approval_id,
        "write_file",
        {
            "workspace_id": context.workspace_id,
            "path": path,
            "content_sha256": hashlib.sha256(content_bytes).hexdigest(),
            "expected_version": expected_version,
        },
        title=f"Approve file write: [{context.workspace_id}] {path}",
        details={"workspace_id": context.workspace_id, "path": path, "bytes_requested": len(content_bytes)},
    )
    if gate is not None:
        return gate
    result = _observed(
        "write_file",
        f"Write file: {path}",
        lambda: context.files.write_file(path, content, expected_version),
        details={"path": path, "bytes_requested": len(content_bytes)},
        summarize=lambda value: {"path": value.get("path"), "bytes": value.get("bytes")},
        workspace_id=context.workspace_id,
    )
    result = {"workspace_id": context.workspace_id, **result}
    activity.emit(
        "file_changed",
        status="completed",
        title=f"File written: {result.get('path', path)}",
        component="files",
        details={"workspace_id": context.workspace_id, "path": result.get("path", path), "bytes": result.get("bytes")},
    )
    return result


@mcp.tool()
def apply_patch(
    patch: str,
    expected_versions: dict[str, str | None],
    approval_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Apply a validated multi-file unified diff inside one workspace."""
    context = registry.get(workspace_id)
    gate = _approval_gate(
        settings.confirm_writes,
        approval_id,
        "apply_patch",
        {
            "workspace_id": context.workspace_id,
            "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
            "expected_versions": expected_versions,
        },
        title=f"Approve multi-file patch: [{context.workspace_id}]",
        details={
            "workspace_id": context.workspace_id,
            "files": sorted(expected_versions.keys()),
            "patch_bytes": len(patch.encode("utf-8")),
        },
    )
    if gate is not None:
        return gate
    result = _observed(
        "apply_patch",
        "Apply multi-file patch",
        lambda: context.patches.apply_patch(patch, expected_versions),
        details={"expected_files": sorted(expected_versions.keys())},
        summarize=lambda value: {
            "applied": value.get("applied", False),
            "files": [{"path": item.get("path"), "action": item.get("action")} for item in value.get("files", [])],
        },
        workspace_id=context.workspace_id,
    )
    result = {"workspace_id": context.workspace_id, **result}
    activity.emit(
        "file_changed",
        status="completed",
        title="Patch changed files",
        component="files",
        details={
            "workspace_id": context.workspace_id,
            "files": [{"path": item.get("path"), "action": item.get("action")} for item in result.get("files", [])],
        },
    )
    return result


@mcp.tool()
def run_command(
    command: str,
    cwd: str = ".",
    background: bool = False,
    timeout_ms: int = 120_000,
    approval_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Run a command in the selected workspace PTY; optionally requires approval."""
    context = registry.get(workspace_id)
    gate = _approval_gate(
        settings.confirm_commands,
        approval_id,
        "run_command",
        {
            "workspace_id": context.workspace_id,
            "command": command,
            "cwd": cwd,
            "background": background,
            "timeout_ms": timeout_ms,
        },
        title=f"Approve terminal command: [{context.workspace_id}]",
        details={
            "workspace_id": context.workspace_id,
            "cwd": cwd,
            "background": background,
            "command_preview": redact_text(command, 240),
        },
    )
    if gate is not None:
        return gate
    return _observed(
        "run_command",
        "Run terminal command",
        lambda: context.terminal.run_command(command, cwd, background, timeout_ms),
        details={"cwd": cwd, "background": background, "command_preview": redact_text(command, 240)},
        summarize=lambda result: {
            "command_id": result.get("command_id"),
            "status": result.get("status"),
            "exit_code": result.get("exit_code"),
        },
        workspace_id=context.workspace_id,
    )


@mcp.tool()
def get_command_output(command_id: str, offset: int = 0, max_bytes: int = 32_768) -> dict[str, Any]:
    """Read incremental terminal output; command_id resolves its workspace."""
    return registry.get_command_output(command_id, offset, max_bytes)


@mcp.tool()
def send_command_input(command_id: str, input: str, append_newline: bool = True) -> dict[str, Any]:
    """Send input to a running command; command_id resolves its workspace."""
    return _observed(
        "send_command_input",
        "Send terminal input",
        lambda: registry.send_command_input(command_id, input, append_newline),
        details={"command_id": command_id, "append_newline": append_newline, "input_bytes": len(input.encode("utf-8"))},
        summarize=lambda result: {
            "workspace_id": result.get("workspace_id"),
            "command_id": result.get("command_id"),
            "input_seq": result.get("input_seq"),
        },
    )


@mcp.tool()
def wait(command_id: str, timeout_ms: int = 30_000) -> dict[str, Any]:
    """Wait for terminal completion or new output; command_id resolves its workspace."""
    return registry.wait(command_id, timeout_ms)


@mcp.tool()
def terminate_command(command_id: str, force: bool = False) -> dict[str, Any]:
    """Terminate a running command; command_id resolves its workspace."""
    return _observed(
        "terminate_command",
        "Terminate terminal command",
        lambda: registry.terminate(command_id, force),
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
    """Return local dashboard availability and address."""
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
        "workspace_count": registry.list_workspaces()["count"],
    }


@mcp.tool()
def vscode_health(workspace_id: str | None = None) -> dict[str, Any]:
    """Return VS Code Companion readiness for one workspace or aggregate state."""
    if workspace_id is None:
        return _observed(
            "vscode_health",
            "Check VS Code Companion workspaces",
            registry.vscode_health,
            summarize=lambda result: {
                "provider_state": result.get("provider_state"),
                "workspace_count": len(result.get("workspaces", [])),
            },
        )
    context = registry.get(workspace_id)
    return _observed(
        "vscode_health",
        "Check VS Code Companion",
        context.vscode.health,
        summarize=lambda result: {"provider_state": result.get("provider_state"), "reason": result.get("provider_state_reason")},
        workspace_id=context.workspace_id,
    )


@mcp.tool()
def get_diagnostics(
    path: str | None = None,
    severity: list[str] | None = None,
    max_results: int = 100,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Read live VS Code diagnostics for the selected workspace."""
    context = registry.get(workspace_id)
    return _observed(
        "get_diagnostics",
        f"Read VS Code diagnostics{': ' + path if path else ''}",
        lambda: context.vscode.get_diagnostics(path, severity, max_results),
        details={"path": path, "severity": severity, "max_results": max_results},
        summarize=lambda result: {
            "provider_state": result.get("provider_state"),
            "results": len(result.get("results", [])),
            "truncated": result.get("truncated", False),
        },
        workspace_id=context.workspace_id,
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
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Query VS Code language providers for the selected workspace."""
    context = registry.get(workspace_id)
    return _observed(
        "lsp",
        f"VS Code LSP: {operation}",
        lambda: context.vscode.lsp(operation, path, line, column, query, include_declaration, max_results),
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
        workspace_id=context.workspace_id,
    )


@mcp.tool()
def read_editor_buffer(
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
    max_bytes: int = 262_144,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Read the current VS Code buffer for a file in the selected workspace."""
    context = registry.get(workspace_id)
    return _observed(
        "read_editor_buffer",
        f"Read editor buffer: {path}",
        lambda: context.vscode.read_editor_buffer(path, start_line, end_line, max_bytes),
        details={"path": path, "start_line": start_line, "end_line": end_line},
        summarize=lambda result: {
            "provider_state": result.get("provider_state"),
            "document_dirty": result.get("document_dirty"),
            "truncated": result.get("truncated", False),
        },
        workspace_id=context.workspace_id,
    )


if __name__ == "__main__":
    app, token = build_secure_mcp_app(mcp)
    host = settings.bridge_host
    port = settings.bridge_port
    dashboard_state = dashboard.start()
    workspaces = registry.list_workspaces()
    activity.emit(
        "system_state",
        status="ready",
        title="MCP Bridge starting",
        component="bridge",
        details={
            "workspace_root": str(settings.workspace_root),
            "workspace_count": workspaces["count"],
            "workspace_ids": [item["workspace_id"] for item in workspaces["workspaces"]],
            "allow_write": settings.allow_write,
            "allow_commands": settings.allow_commands,
            "bridge_host": host,
            "bridge_port": port,
            "confirm_writes": settings.confirm_writes,
            "confirm_commands": settings.confirm_commands,
        },
    )
    print(f"Local MCP endpoint: http://{host}:{port}/{token}/mcp")
    print(f"Configured workspaces: {workspaces['count']} ({', '.join(item['workspace_id'] for item in workspaces['workspaces'])})")
    if dashboard_state.get("enabled"):
        print(f"Observability dashboard: {dashboard_state['url']} (localhost-only control plane)")
    if settings.allowed_hosts:
        print(f"Extra allowed Host headers: {', '.join(settings.allowed_hosts)}")
    print("The token is a capability secret. Do not publish or commit it.")
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        terminal_state = registry.shutdown(timeout=3.0)
        activity.emit(
            "system_state",
            status="stopped",
            title="Terminal services stopped",
            component="terminal",
            details=terminal_state,
        )
        dashboard.stop()