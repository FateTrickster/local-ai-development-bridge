from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import Settings
from .pathguard import WorkspaceGuard


def _data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "LocalAIDevelopmentBridge"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "LocalAIDevelopmentBridge"


class VSCodeService:
    def __init__(
        self,
        settings: Settings,
        guard: WorkspaceGuard,
        *,
        data_dir: Path | None = None,
        timeout_seconds: float = 3.0,
    ):
        self.settings = settings
        self.guard = guard
        self.data_dir = (data_dir or _data_dir()).resolve()
        self.connection_file = self.data_dir / "vscode-companion.json"
        self.token_file = self.data_dir / "vscode-token.txt"
        self.timeout_seconds = max(0.2, min(timeout_seconds, 30.0))

    @staticmethod
    def _not_ready(reason: str, **extra: Any) -> dict[str, Any]:
        return {
            "provider_state": "not_ready",
            "provider_state_reason": reason,
            "semantic_result_inconclusive": True,
            "results": [],
            "truncated": False,
            **extra,
        }

    def _connection(self) -> tuple[int, str]:
        if not self.connection_file.is_file():
            raise RuntimeError("VSCODE_COMPANION_NOT_RUNNING")
        if not self.token_file.is_file():
            raise RuntimeError("VSCODE_COMPANION_TOKEN_MISSING")
        try:
            info = json.loads(self.connection_file.read_text(encoding="utf-8"))
            port = int(info["port"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("VSCODE_COMPANION_STATE_INVALID") from exc
        if port < 1024 or port > 65535:
            raise RuntimeError("VSCODE_COMPANION_PORT_INVALID")
        try:
            token = self.token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("VSCODE_COMPANION_TOKEN_UNREADABLE") from exc
        if not token:
            raise RuntimeError("VSCODE_COMPANION_TOKEN_EMPTY")
        return port, token

    def _request(self, endpoint: str, payload: dict[str, Any] | None = None, *, method: str = "POST") -> dict[str, Any]:
        port, token = self._connection()
        url = f"http://127.0.0.1:{port}{endpoint}"
        data = None
        headers = {
            "x-local-ai-bridge-token": token,
            "accept": "application/json",
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["content-type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise RuntimeError("VSCODE_COMPANION_RESPONSE_TOO_LARGE")
                parsed = json.loads(raw.decode("utf-8"))
                if not isinstance(parsed, dict):
                    raise RuntimeError("VSCODE_COMPANION_RESPONSE_INVALID")
                return parsed
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read(4096).decode("utf-8", errors="replace")
            except Exception:
                detail = ""
            raise RuntimeError(f"VSCODE_COMPANION_HTTP_{exc.code}: {detail[:1000]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"VSCODE_COMPANION_UNREACHABLE: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("VSCODE_COMPANION_RESPONSE_INVALID_JSON") from exc

    def _absolute_workspace_path(self, relative: str) -> str:
        return str(self.guard.resolve(relative))

    def health(self) -> dict[str, Any]:
        try:
            result = self._request("/health", method="GET")
        except RuntimeError as exc:
            return self._not_ready(str(exc))
        roots = [str(Path(item).resolve()) for item in result.get("workspace_folders", []) if isinstance(item, str)]
        bridge_root = str(self.settings.workspace_root.resolve())
        if roots and not any(
            bridge_root.casefold() == root.casefold()
            or bridge_root.casefold().startswith(root.casefold() + os.sep.casefold())
            or root.casefold().startswith(bridge_root.casefold() + os.sep.casefold())
            for root in roots
        ):
            return self._not_ready(
                "VSCODE_WORKSPACE_MISMATCH",
                companion_workspace_folders=roots,
                bridge_workspace_root=bridge_root,
            )
        return {
            "provider_state": "ready",
            "provider_state_reason": "VS Code Companion reachable",
            **result,
        }

    def get_diagnostics(
        self,
        path: str | None = None,
        severity: list[str] | None = None,
        max_results: int = 100,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "max_results": max(1, min(max_results, 500)),
            "workspace_root": str(self.settings.workspace_root),
        }
        if path:
            payload["path"] = self._absolute_workspace_path(path)
        if severity:
            allowed = {"error", "warning", "information", "hint"}
            normalized = [str(item).lower() for item in severity]
            if any(item not in allowed for item in normalized):
                raise ValueError("INVALID_DIAGNOSTIC_SEVERITY")
            payload["severity"] = normalized
        try:
            return self._request("/diagnostics", payload)
        except RuntimeError as exc:
            return self._not_ready(str(exc))

    def lsp(
        self,
        operation: str,
        path: str | None = None,
        line: int | None = None,
        column: int | None = None,
        query: str | None = None,
        include_declaration: bool = True,
        max_results: int = 100,
    ) -> dict[str, Any]:
        allowed = {
            "workspace_symbols",
            "document_symbols",
            "definition",
            "references",
            "implementation",
            "hover",
        }
        if operation not in allowed:
            raise ValueError(f"UNKNOWN_LSP_OPERATION: {operation}")
        payload: dict[str, Any] = {
            "operation": operation,
            "include_declaration": bool(include_declaration),
            "max_results": max(1, min(max_results, 500)),
            "workspace_root": str(self.settings.workspace_root),
        }
        if path:
            payload["path"] = self._absolute_workspace_path(path)
        if line is not None:
            payload["line"] = max(1, int(line))
        if column is not None:
            payload["column"] = max(1, int(column))
        if query is not None:
            payload["query"] = str(query)
        try:
            return self._request("/lsp", payload)
        except RuntimeError as exc:
            return self._not_ready(str(exc), operation=operation)

    def read_editor_buffer(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
        max_bytes: int = 262_144,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": self._absolute_workspace_path(path),
            "start_line": max(1, int(start_line)),
            "max_bytes": max(1024, min(int(max_bytes), 4 * 1024 * 1024)),
            "workspace_root": str(self.settings.workspace_root),
        }
        if end_line is not None:
            payload["end_line"] = max(payload["start_line"], int(end_line))
        try:
            return self._request("/document", payload)
        except RuntimeError as exc:
            return self._not_ready(str(exc), path=path)
