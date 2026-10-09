from __future__ import annotations

import os
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from bridge.config import Settings
from bridge.file_service import FileService
from bridge.patch_service import PatchService
from bridge.pathguard import WorkspaceGuard
from bridge.security import build_secure_mcp_app
from bridge.task_service import TaskService
from bridge.terminal_service import TerminalService
from bridge.vscode_service import VSCodeService

settings = Settings.from_env()
guard = WorkspaceGuard(settings.workspace_root)
files = FileService(settings, guard)
patches = PatchService(settings, guard)
terminal = TerminalService(settings, guard)
tasks = TaskService()
vscode_service = VSCodeService(settings, guard)

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
    return files.list_directory(path, cursor, limit, include_hidden, include_ignored)


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
    return files.find_files(patterns, path, exclude, include_hidden, include_ignored, sort, max_results)


@mcp.tool()
def read_files(requests: list[dict[str, Any]]) -> dict[str, Any]:
    """Read 1..20 UTF-8 files/ranges and return raw-byte SHA-256 versions."""
    return files.read_files(requests)


@mcp.tool()
def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> dict[str, Any]:
    """Compatibility single-file reader with SHA-256 version metadata."""
    return files.read_file(path, start_line, end_line)


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
    return files.search_files(
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
    )


@mcp.tool()
def write_file(path: str, content: str, expected_version: str | None = None) -> dict[str, Any]:
    """Create/overwrite UTF-8 text when writes are enabled; optionally enforce a SHA-256 expected version."""
    return files.write_file(path, content, expected_version)


@mcp.tool()
def apply_patch(patch: str, expected_versions: dict[str, str | None]) -> dict[str, Any]:
    """Apply a validated multi-file unified diff transaction using optimistic version checks."""
    return patches.apply_patch(patch, expected_versions)


@mcp.tool()
def run_command(
    command: str,
    cwd: str = ".",
    background: bool = False,
    timeout_ms: int = 120_000,
) -> dict[str, Any]:
    """Run a command in a persistent native PTY; timeout only bounds this call's wait."""
    return terminal.run_command(command, cwd, background, timeout_ms)


@mcp.tool()
def get_command_output(command_id: str, offset: int = 0, max_bytes: int = 32_768) -> dict[str, Any]:
    """Read incremental terminal output using absolute UTF-8 byte offsets."""
    return terminal.get_command_output(command_id, offset, max_bytes)


@mcp.tool()
def send_command_input(command_id: str, input: str, append_newline: bool = True) -> dict[str, Any]:
    """Send interactive UTF-8 input to a running PTY command."""
    return terminal.send_command_input(command_id, input, append_newline)


@mcp.tool()
def wait(command_id: str, timeout_ms: int = 30_000) -> dict[str, Any]:
    """Wait for terminal completion or new output without terminating the underlying process."""
    return terminal.wait(command_id, timeout_ms)


@mcp.tool()
def terminate_command(command_id: str, force: bool = False) -> dict[str, Any]:
    """Terminate a running PTY command."""
    return terminal.terminate(command_id, force)


@mcp.tool()
def set_todos(todos: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace the durable task snapshot; at most one todo may be in_progress."""
    return tasks.set_todos(todos)


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
    return tasks.report_progress(message, todo_id, current, total, phase, phase_total)


@mcp.tool()
def get_task_state() -> dict[str, Any]:
    """Return the current durable todo snapshot and progress event count."""
    return tasks.get_state()


@mcp.tool()
def vscode_health() -> dict[str, Any]:
    """Return VS Code Companion readiness, workspace roots and open-document state."""
    return vscode_service.health()


@mcp.tool()
def get_diagnostics(
    path: str | None = None,
    severity: list[str] | None = None,
    max_results: int = 100,
) -> dict[str, Any]:
    """Read live VS Code diagnostics, including dirty editor state when available."""
    return vscode_service.get_diagnostics(path, severity, max_results)


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
    return vscode_service.lsp(
        operation,
        path,
        line,
        column,
        query,
        include_declaration,
        max_results,
    )


@mcp.tool()
def read_editor_buffer(
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
    max_bytes: int = 262_144,
) -> dict[str, Any]:
    """Read the current VS Code text buffer, including unsaved edits, with bounded output."""
    return vscode_service.read_editor_buffer(path, start_line, end_line, max_bytes)


if __name__ == "__main__":
    app, token = build_secure_mcp_app(mcp)
    host = settings.bridge_host
    port = settings.bridge_port
    print(f"Local MCP endpoint: http://{host}:{port}/{token}/mcp")
    if settings.allowed_hosts:
        print(f"Extra allowed Host headers: {', '.join(settings.allowed_hosts)}")
    print("The token is a capability secret. Do not publish or commit it.")
    uvicorn.run(app, host=host, port=port, log_level="info")
