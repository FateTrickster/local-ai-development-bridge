from __future__ import annotations

import os
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

ROOT = Path(os.environ.get("WORKSPACE_ROOT", Path.cwd().parent)).resolve()
ALLOW_WRITE = os.environ.get("ALLOW_WRITE", "0") == "1"
ALLOW_COMMANDS = os.environ.get("ALLOW_COMMANDS", "0") == "1"

mcp = FastMCP("Local Workspace MCP")


def safe_path(relative: str = ".") -> Path:
    candidate = (ROOT / relative).resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("Path escapes WORKSPACE_ROOT") from exc
    return candidate


@mcp.tool()
def workspace_info() -> dict:
    """Return the configured workspace root and permission state."""
    return {
        "workspace_root": str(ROOT),
        "allow_write": ALLOW_WRITE,
        "allow_commands": ALLOW_COMMANDS,
    }


@mcp.tool()
def list_directory(path: str = ".") -> list[dict]:
    """List one directory inside the configured workspace."""
    target = safe_path(path)
    if not target.is_dir():
        raise ValueError(f"Not a directory: {path}")
    out = []
    for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        out.append({
            "name": item.name,
            "path": str(item.relative_to(ROOT)).replace("\\", "/"),
            "type": "directory" if item.is_dir() else "file",
            "size": item.stat().st_size if item.is_file() else None,
        })
    return out


@mcp.tool()
def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> str:
    """Read a UTF-8 text file with optional 1-based inclusive line range."""
    target = safe_path(path)
    if not target.is_file():
        raise ValueError(f"Not a file: {path}")
    text = target.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = max(1, start_line) - 1
    stop = len(lines) if end_line is None else min(len(lines), max(start_line, end_line))
    return "\n".join(lines[start:stop])


@mcp.tool()
def search_files(pattern: str, path: str = ".", max_results: int = 100) -> list[dict]:
    """Search UTF-8 text files for a literal string."""
    base = safe_path(path)
    results: list[dict] = []
    for file in base.rglob("*"):
        if len(results) >= max_results:
            break
        if not file.is_file():
            continue
        try:
            content = file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for idx, line in enumerate(content.splitlines(), start=1):
            if pattern in line:
                results.append({
                    "path": str(file.relative_to(ROOT)).replace("\\", "/"),
                    "line": idx,
                    "text": line[:500],
                })
                if len(results) >= max_results:
                    break
    return results


@mcp.tool()
def write_file(path: str, content: str) -> dict:
    """Create or overwrite a UTF-8 text file when ALLOW_WRITE=1."""
    if not ALLOW_WRITE:
        raise PermissionError("Writes are disabled. Set ALLOW_WRITE=1 to enable.")
    target = safe_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": str(target.relative_to(ROOT)).replace("\\", "/"), "bytes": len(content.encode("utf-8"))}


@mcp.tool()
def run_command(command: str, cwd: str = ".", timeout_seconds: int = 120) -> dict:
    """Run a shell command inside the workspace when ALLOW_COMMANDS=1."""
    if not ALLOW_COMMANDS:
        raise PermissionError("Command execution is disabled. Set ALLOW_COMMANDS=1 to enable.")
    workdir = safe_path(cwd)
    if not workdir.is_dir():
        raise ValueError(f"Not a directory: {cwd}")
    completed = subprocess.run(
        command,
        cwd=workdir,
        shell=True,
        capture_output=True,
        text=True,
        timeout=max(1, min(timeout_seconds, 120)),
    )
    return {
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-20000:],
        "stderr": completed.stderr[-20000:],
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
