from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from unidiff import PatchSet

from .config import Settings
from .file_service import sha256_file
from .pathguard import WorkspaceGuard


class PatchService:
    def __init__(self, settings: Settings, guard: WorkspaceGuard):
        self.settings = settings
        self.guard = guard

    @staticmethod
    def _normalize_patch_path(value: str | None) -> str | None:
        if not value or value == "/dev/null":
            return None
        value = value.replace("\\", "/")
        if value.startswith("a/") or value.startswith("b/"):
            value = value[2:]
        while value.startswith("./"):
            value = value[2:]
        return value

    def _affected_files(self, patch_text: str) -> list[dict[str, Any]]:
        try:
            patch_set = PatchSet(patch_text.splitlines(keepends=True))
        except Exception as exc:
            raise ValueError(f"INVALID_PATCH: {exc}") from exc
        if not patch_set:
            raise ValueError("INVALID_PATCH: no file changes")

        affected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for patched in patch_set:
            if getattr(patched, "is_binary_file", False):
                raise ValueError("BINARY_PATCH_NOT_SUPPORTED")
            source = self._normalize_patch_path(patched.source_file)
            target = self._normalize_patch_path(patched.target_file)
            relative = target or source
            if relative is None:
                raise ValueError("INVALID_PATCH_PATH")
            resolved = self.guard.resolve(relative)
            normalized = self.guard.relative(resolved)
            if normalized in seen:
                continue
            seen.add(normalized)
            if source is None:
                action = "add"
            elif target is None:
                action = "delete"
            else:
                action = "update"
            affected.append({"path": normalized, "action": action, "resolved": resolved})
        return affected

    def apply_patch(self, patch: str, expected_versions: dict[str, str | None] | None = None) -> dict[str, Any]:
        if not self.settings.allow_write:
            raise PermissionError("WRITES_DISABLED")
        if not patch.strip():
            raise ValueError("INVALID_PATCH: empty patch")
        expected_versions = expected_versions or {}
        affected = self._affected_files(patch)

        snapshots: dict[str, bytes | None] = {}
        before_versions: dict[str, str | None] = {}
        for item in affected:
            relative = item["path"]
            target: Path = item["resolved"]
            exists = target.is_file()
            current = sha256_file(target) if exists else None
            before_versions[relative] = current
            snapshots[relative] = target.read_bytes() if exists else None

            if relative not in expected_versions:
                raise ValueError(f"EXPECTED_VERSION_REQUIRED: {relative}")
            expected = expected_versions[relative]
            if expected != current:
                raise ValueError(f"VERSION_CONFLICT: {relative} expected={expected} current={current}")
            if item["action"] == "add" and exists:
                raise ValueError(f"PATCH_ADD_TARGET_EXISTS: {relative}")
            if item["action"] in {"update", "delete"} and not exists:
                raise ValueError(f"PATCH_SOURCE_MISSING: {relative}")

        patch_file: str | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", suffix=".patch", delete=False) as handle:
                handle.write(patch)
                patch_file = handle.name

            check = subprocess.run(
                ["git", "apply", "--check", "--recount", "--whitespace=nowarn", patch_file],
                cwd=self.settings.workspace_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if check.returncode != 0:
                message = (check.stderr or check.stdout).strip()
                raise ValueError(f"PATCH_CHECK_FAILED: {message}")

            applied = subprocess.run(
                ["git", "apply", "--recount", "--whitespace=nowarn", patch_file],
                cwd=self.settings.workspace_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if applied.returncode != 0:
                message = (applied.stderr or applied.stdout).strip()
                raise RuntimeError(f"PATCH_APPLY_FAILED: {message}")

            files: list[dict[str, Any]] = []
            for item in affected:
                relative = item["path"]
                target: Path = item["resolved"]
                after = sha256_file(target) if target.is_file() else None
                files.append(
                    {
                        "action": item["action"],
                        "path": relative,
                        "before_version": before_versions[relative],
                        "after_version": after,
                        "status": "applied",
                    }
                )
            return {
                "applied": True,
                "rolled_back": False,
                "recovery_required": False,
                "files": files,
            }
        except Exception:
            recovery_required = False
            for item in affected:
                relative = item["path"]
                target: Path = item["resolved"]
                before = snapshots.get(relative)
                try:
                    if before is None:
                        if target.exists():
                            target.unlink()
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(before)
                except OSError:
                    recovery_required = True
            if recovery_required:
                raise RuntimeError("PATCH_FAILED_AND_ROLLBACK_INCOMPLETE")
            raise
        finally:
            if patch_file:
                try:
                    os.unlink(patch_file)
                except OSError:
                    pass
