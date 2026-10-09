from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .activity_service import ActivityService
from .config import Settings
from .file_service import FileService
from .patch_service import PatchService
from .pathguard import WorkspaceGuard
from .terminal_service import TerminalService
from .vscode_service import VSCodeService


_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass
class WorkspaceContext:
    workspace_id: str
    name: str
    root: Path
    settings: Settings
    guard: WorkspaceGuard
    files: FileService
    patches: PatchService
    terminal: TerminalService
    vscode: VSCodeService

    def public_info(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "workspace_root": str(self.root),
            "allow_write": self.settings.allow_write,
            "allow_commands": self.settings.allow_commands,
        }


def _normalize_workspace_id(value: str) -> str:
    workspace_id = str(value).strip()
    if not _WORKSPACE_ID.fullmatch(workspace_id):
        raise ValueError(f"INVALID_WORKSPACE_ID: {workspace_id!r}")
    return workspace_id


def _paths_overlap(left: Path, right: Path) -> bool:
    left = left.resolve()
    right = right.resolve()
    try:
        common = Path(os.path.commonpath([str(left), str(right)])).resolve()
    except ValueError:
        return False
    left_key = os.path.normcase(str(left))
    right_key = os.path.normcase(str(right))
    common_key = os.path.normcase(str(common))
    return common_key in {left_key, right_key}


def parse_extra_workspaces(raw: str | None) -> list[dict[str, str]]:
    """Parse BRIDGE_WORKSPACES_JSON into normalized extra-workspace specs.

    The primary workspace always comes from WORKSPACE_ROOT and has id `default`.
    This environment value only describes additional roots, keeping the existing
    single-workspace configuration fully backward compatible.
    """
    if not raw or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("INVALID_BRIDGE_WORKSPACES_JSON") from exc
    if not isinstance(parsed, list):
        raise ValueError("BRIDGE_WORKSPACES_JSON_MUST_BE_LIST")
    if len(parsed) > 32:
        raise ValueError("TOO_MANY_WORKSPACES")

    result: list[dict[str, str]] = []
    seen_ids = {"default"}
    seen_roots: list[Path] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError("INVALID_WORKSPACE_SPEC")
        workspace_id = _normalize_workspace_id(str(item.get("id", "")))
        if workspace_id == "default" or workspace_id in seen_ids:
            raise ValueError(f"DUPLICATE_WORKSPACE_ID: {workspace_id}")
        raw_path = str(item.get("path", "")).strip()
        if not raw_path:
            raise ValueError(f"WORKSPACE_PATH_REQUIRED: {workspace_id}")
        root = Path(raw_path).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"WORKSPACE_NOT_DIRECTORY: {workspace_id}: {root}")
        if any(_paths_overlap(root, existing) for existing in seen_roots):
            raise ValueError(f"OVERLAPPING_WORKSPACE_ROOT: {root}")
        name = str(item.get("name") or workspace_id).strip()[:120] or workspace_id
        seen_ids.add(workspace_id)
        seen_roots.append(root)
        result.append({"id": workspace_id, "name": name, "path": str(root)})
    return result


class WorkspaceRegistry:
    """Explicit workspace registry used by all path-bearing MCP operations.

    `default` preserves the legacy WORKSPACE_ROOT behavior. Additional roots are
    selected by an explicit workspace_id; paths never implicitly cross roots.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        activity: ActivityService | None = None,
        extra_workspaces: list[dict[str, str]] | None = None,
        vscode_data_dir: Path | None = None,
    ):
        self.base_settings = settings
        self.activity = activity
        self._contexts: dict[str, WorkspaceContext] = {}
        self._roots: list[Path] = []
        self._add_context(
            "default",
            "Default workspace",
            settings.workspace_root,
            vscode_data_dir=vscode_data_dir,
        )
        for spec in extra_workspaces or []:
            self._add_context(
                _normalize_workspace_id(spec["id"]),
                str(spec.get("name") or spec["id"]),
                Path(spec["path"]),
                vscode_data_dir=vscode_data_dir,
            )

    @classmethod
    def from_env(
        cls,
        settings: Settings,
        *,
        activity: ActivityService | None = None,
        vscode_data_dir: Path | None = None,
    ) -> "WorkspaceRegistry":
        extras = parse_extra_workspaces(os.environ.get("BRIDGE_WORKSPACES_JSON"))
        return cls(settings, activity=activity, extra_workspaces=extras, vscode_data_dir=vscode_data_dir)

    def _add_context(
        self,
        workspace_id: str,
        name: str,
        root: Path,
        *,
        vscode_data_dir: Path | None,
    ) -> None:
        workspace_id = _normalize_workspace_id(workspace_id)
        root = Path(root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"WORKSPACE_NOT_DIRECTORY: {workspace_id}: {root}")
        if workspace_id in self._contexts:
            raise ValueError(f"DUPLICATE_WORKSPACE_ID: {workspace_id}")
        if any(_paths_overlap(root, existing) for existing in self._roots):
            raise ValueError(f"OVERLAPPING_WORKSPACE_ROOT: {root}")
        context_settings = replace(self.base_settings, workspace_root=root)
        guard = WorkspaceGuard(root)
        context = WorkspaceContext(
            workspace_id=workspace_id,
            name=str(name).strip()[:120] or workspace_id,
            root=root,
            settings=context_settings,
            guard=guard,
            files=FileService(context_settings, guard),
            patches=PatchService(context_settings, guard),
            terminal=TerminalService(context_settings, guard, activity=self.activity, workspace_id=workspace_id),
            vscode=VSCodeService(context_settings, guard, data_dir=vscode_data_dir),
        )
        self._contexts[workspace_id] = context
        self._roots.append(root)

    @property
    def default(self) -> WorkspaceContext:
        return self._contexts["default"]

    def get(self, workspace_id: str | None = None) -> WorkspaceContext:
        requested = "default" if workspace_id is None or not str(workspace_id).strip() else str(workspace_id).strip()
        context = self._contexts.get(requested)
        if context is None:
            raise KeyError(f"UNKNOWN_WORKSPACE_ID: {requested}")
        return context

    def list_workspaces(self, *, include_vscode: bool = False) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for context in self._contexts.values():
            info = context.public_info()
            if include_vscode:
                health = context.vscode.health()
                info["vscode"] = {
                    "provider_state": health.get("provider_state"),
                    "provider_state_reason": health.get("provider_state_reason"),
                    "workspace_folders": health.get("workspace_folders", []),
                }
            items.append(info)
        return {
            "default_workspace_id": "default",
            "workspaces": items,
            "count": len(items),
        }

    def workspace_info(self, workspace_id: str | None = None) -> dict[str, Any]:
        context = self.get(workspace_id)
        return {
            **context.files.workspace_info(),
            "workspace_id": context.workspace_id,
            "workspace_name": context.name,
            "available_workspaces": len(self._contexts),
        }

    def _terminal_for_command(self, command_id: str) -> tuple[WorkspaceContext, TerminalService]:
        for context in self._contexts.values():
            try:
                context.terminal.get_command_output(command_id, 0, 1)
            except KeyError:
                continue
            return context, context.terminal
        raise KeyError(f"UNKNOWN_COMMAND_ID: {command_id}")

    def get_command_output(self, command_id: str, offset: int = 0, max_bytes: int = 32_768) -> dict[str, Any]:
        context, terminal = self._terminal_for_command(command_id)
        return {"workspace_id": context.workspace_id, **terminal.get_command_output(command_id, offset, max_bytes)}

    def send_command_input(self, command_id: str, input: str, append_newline: bool = True) -> dict[str, Any]:
        context, terminal = self._terminal_for_command(command_id)
        return {"workspace_id": context.workspace_id, **terminal.send_command_input(command_id, input, append_newline)}

    def wait(self, command_id: str, timeout_ms: int = 30_000) -> dict[str, Any]:
        context, terminal = self._terminal_for_command(command_id)
        return {"workspace_id": context.workspace_id, **terminal.wait(command_id, timeout_ms)}

    def terminate(self, command_id: str, force: bool = False) -> dict[str, Any]:
        context, terminal = self._terminal_for_command(command_id)
        return {"workspace_id": context.workspace_id, **terminal.terminate(command_id, force)}

    def list_commands(self, limit: int = 20, tail_bytes: int = 4096) -> dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        combined: list[dict[str, Any]] = []
        total = 0
        for context in self._contexts.values():
            result = context.terminal.list_commands(limit=limit, tail_bytes=tail_bytes)
            total += int(result.get("total_tracked", 0))
            for item in result.get("commands", []):
                combined.append({"workspace_id": context.workspace_id, **item})
        combined.sort(key=lambda item: str(item.get("started_at", "")), reverse=True)
        return {"commands": combined[:limit], "total_tracked": total}

    def vscode_health(self) -> dict[str, Any]:
        default_health = self.default.vscode.health()
        return {
            **default_health,
            "workspace_id": "default",
            "workspaces": [
                {"workspace_id": context.workspace_id, **context.vscode.health()}
                for context in self._contexts.values()
            ],
        }

    def shutdown(self, timeout: float = 3.0) -> dict[str, Any]:
        results = []
        for context in self._contexts.values():
            results.append({"workspace_id": context.workspace_id, **context.terminal.shutdown(timeout=timeout)})
        return {"workspaces": results, "count": len(results)}
