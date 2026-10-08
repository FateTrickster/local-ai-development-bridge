from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .pathguard import WorkspaceGuard


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _looks_binary(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    sample = data[:4096]
    if not sample:
        return False
    text_bytes = set(b"\n\r\t\b\f") | set(range(32, 127)) | set(range(128, 256))
    return sum(byte not in text_bytes for byte in sample) / len(sample) > 0.30


class FileService:
    def __init__(self, settings: Settings, guard: WorkspaceGuard):
        self.settings = settings
        self.guard = guard

    def workspace_info(self) -> dict[str, Any]:
        return {
            "workspace_root": str(self.settings.workspace_root),
            "allow_write": self.settings.allow_write,
            "allow_commands": self.settings.allow_commands,
            "max_read_bytes": self.settings.max_read_bytes,
            "max_search_results": self.settings.max_search_results,
        }

    def list_directory(
        self,
        path: str = ".",
        cursor: str | None = None,
        limit: int = 100,
        include_hidden: bool = False,
        include_ignored: bool = False,
    ) -> dict[str, Any]:
        target = self.guard.resolve(path)
        if not target.is_dir():
            raise ValueError(f"NOT_A_DIRECTORY: {path}")
        limit = max(1, min(limit, self.settings.max_directory_entries))
        last_name = self.guard.decode_cursor(cursor)
        entries = [
            item
            for item in target.iterdir()
            if self.guard.include(item, include_hidden=include_hidden, include_ignored=include_ignored)
        ]
        entries.sort(key=lambda item: (item.name.casefold(), item.name))
        if last_name is not None:
            last_key = (last_name.casefold(), last_name)
            entries = [item for item in entries if (item.name.casefold(), item.name) > last_key]

        page = entries[:limit]
        result_entries: list[dict[str, Any]] = []
        for item in page:
            stat = item.stat()
            result_entries.append(
                {
                    "name": item.name,
                    "path": self.guard.relative(item),
                    "type": "directory" if item.is_dir() else "file",
                    "size": stat.st_size if item.is_file() else None,
                    "modified_at": _iso_mtime(item),
                }
            )
        next_cursor = None
        if len(entries) > limit and page:
            next_cursor = self.guard.encode_cursor(page[-1].name)
        return {
            "path": self.guard.relative(target),
            "entries": result_entries,
            "next_cursor": next_cursor,
            "truncated": len(entries) > limit,
        }

    def _iter_files(
        self,
        base: Path,
        *,
        include_hidden: bool,
        include_ignored: bool,
    ):
        stack = [base]
        while stack:
            current = stack.pop()
            try:
                children = sorted(current.iterdir(), key=lambda item: (item.name.casefold(), item.name), reverse=True)
            except OSError:
                continue
            for item in children:
                if not self.guard.include(item, include_hidden=include_hidden, include_ignored=include_ignored):
                    continue
                if item.is_symlink():
                    continue
                if item.is_dir():
                    stack.append(item)
                elif item.is_file():
                    yield item

    def find_files(
        self,
        patterns: list[str],
        path: str = ".",
        exclude: list[str] | None = None,
        include_hidden: bool = False,
        include_ignored: bool = False,
        sort: str = "path_asc",
        max_results: int = 100,
    ) -> dict[str, Any]:
        if not patterns or len(patterns) > 20:
            raise ValueError("patterns must contain 1..20 items")
        base = self.guard.resolve(path)
        if not base.is_dir():
            raise ValueError(f"NOT_A_DIRECTORY: {path}")
        exclude = exclude or []
        max_results = max(1, min(max_results, 500))
        matches: list[Path] = []
        scanned = 0
        for file in self._iter_files(base, include_hidden=include_hidden, include_ignored=include_ignored):
            scanned += 1
            relative = self.guard.relative(file)
            base_relative = file.relative_to(base).as_posix()
            if self.guard.match_any(relative, exclude) or self.guard.match_any(base_relative, exclude):
                continue
            if self.guard.match_any(relative, patterns) or self.guard.match_any(base_relative, patterns):
                matches.append(file)
        if sort == "modified_desc":
            matches.sort(key=lambda file: file.stat().st_mtime, reverse=True)
        else:
            matches.sort(key=lambda file: (self.guard.relative(file).casefold(), self.guard.relative(file)))
        truncated = len(matches) > max_results
        matches = matches[:max_results]
        return {
            "matches": [
                {
                    "path": self.guard.relative(file),
                    "size": file.stat().st_size,
                    "modified_at": _iso_mtime(file),
                }
                for file in matches
            ],
            "truncated": truncated,
            "scanned_candidates": scanned,
        }

    def read_files(self, files: list[dict[str, Any]]) -> dict[str, Any]:
        if not files or len(files) > 20:
            raise ValueError("files must contain 1..20 items")
        remaining = self.settings.max_read_bytes
        output: list[dict[str, Any]] = []
        total_content_bytes = 0

        for request in files:
            relative = str(request.get("path", ""))
            start_line = max(1, int(request.get("start_line", 1)))
            requested_end = request.get("end_line")
            end_line = int(requested_end) if requested_end is not None else None
            target = self.guard.resolve(relative)
            item: dict[str, Any] = {"ok": False, "path": relative}
            if not target.is_file():
                item["error"] = "NOT_A_FILE"
                output.append(item)
                continue
            raw = target.read_bytes()
            version = f"sha256:{hashlib.sha256(raw).hexdigest()}"
            if _looks_binary(raw):
                item.update({"binary": True, "version": version, "size_bytes": len(raw), "error": "BINARY_FILE"})
                output.append(item)
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                item.update({"binary": True, "version": version, "size_bytes": len(raw), "error": "NON_UTF8_FILE"})
                output.append(item)
                continue

            lines = text.splitlines()
            total_lines = len(lines)
            start_index = min(total_lines, start_line - 1)
            stop_index = total_lines if end_line is None else min(total_lines, max(start_line, end_line))
            selected = "\n".join(lines[start_index:stop_index])
            encoded = selected.encode("utf-8")
            truncated = False
            if len(encoded) > remaining:
                selected = encoded[: max(0, remaining)].decode("utf-8", errors="ignore")
                encoded = selected.encode("utf-8")
                truncated = True
            used = len(encoded)
            remaining -= used
            total_content_bytes += used
            actual_end = start_index + len(selected.splitlines())
            item.update(
                {
                    "ok": True,
                    "content": selected,
                    "version": version,
                    "size_bytes": len(raw),
                    "total_lines": total_lines,
                    "actual_start_line": start_index + 1 if total_lines else 1,
                    "actual_end_line": actual_end,
                    "truncated": truncated or stop_index < total_lines,
                    "complete": not truncated and start_index == 0 and stop_index == total_lines,
                    "binary": False,
                }
            )
            output.append(item)
            if remaining <= 0:
                break

        return {
            "files": output,
            "total_content_bytes": total_content_bytes,
            "max_total_content_bytes": self.settings.max_read_bytes,
        }

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> dict[str, Any]:
        result = self.read_files([{"path": path, "start_line": start_line, "end_line": end_line}])
        return result["files"][0]

    def search_files(
        self,
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
        base = self.guard.resolve(path)
        if not base.is_dir():
            raise ValueError(f"NOT_A_DIRECTORY: {path}")
        glob = glob or []
        context_lines = max(0, min(context_lines, 5))
        max_results = max(1, min(max_results, self.settings.max_search_results))
        max_per_file = max(1, min(max_per_file, 100))
        flags = 0 if case_sensitive else re.IGNORECASE
        compiled = re.compile(pattern, flags) if regex else None
        needle = pattern if case_sensitive else pattern.casefold()
        results: list[dict[str, Any]] = []
        output_bytes = 0
        scanned = 0

        for file in self._iter_files(base, include_hidden=include_hidden, include_ignored=include_ignored):
            if len(results) >= max_results or output_bytes >= self.settings.max_search_bytes:
                break
            relative = self.guard.relative(file)
            base_relative = file.relative_to(base).as_posix()
            if glob and not (self.guard.match_any(relative, glob) or self.guard.match_any(base_relative, glob)):
                continue
            scanned += 1
            try:
                raw = file.read_bytes()
                if _looks_binary(raw):
                    continue
                text = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            lines = text.splitlines()
            per_file = 0
            for index, line in enumerate(lines):
                haystack = line if case_sensitive else line.casefold()
                matched = bool(compiled.search(line)) if compiled else needle in haystack
                if not matched:
                    continue
                start = max(0, index - context_lines)
                end = min(len(lines), index + context_lines + 1)
                context = "\n".join(lines[start:end])
                item = {
                    "path": relative,
                    "line": index + 1,
                    "text": line[:1000],
                    "context": context[:4000],
                }
                size = len(str(item).encode("utf-8"))
                if output_bytes + size > self.settings.max_search_bytes:
                    break
                results.append(item)
                output_bytes += size
                per_file += 1
                if per_file >= max_per_file or len(results) >= max_results:
                    break

        return {
            "results": results,
            "truncated": len(results) >= max_results or output_bytes >= self.settings.max_search_bytes,
            "scanned_files": scanned,
            "output_bytes": output_bytes,
        }

    def write_file(self, path: str, content: str, expected_version: str | None = None) -> dict[str, Any]:
        if not self.settings.allow_write:
            raise PermissionError("WRITES_DISABLED")
        target = self.guard.resolve(path)
        if target.exists() and target.is_dir():
            raise ValueError("TARGET_IS_DIRECTORY")
        current_version = sha256_file(target) if target.exists() else None
        if expected_version is not None and expected_version != current_version:
            raise ValueError(f"VERSION_CONFLICT: expected={expected_version} current={current_version}")
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        target.write_bytes(data)
        return {
            "path": self.guard.relative(target),
            "bytes": len(data),
            "before_version": current_version,
            "after_version": sha256_file(target),
        }
