from __future__ import annotations

import base64
import json
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

import pathspec


_DEFAULT_IGNORES = (
    ".git/",
    ".venv/",
    "node_modules/",
    "__pycache__/",
    ".runtime/",
    ".audit/",
)


class WorkspaceGuard:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._ignore_spec = self._load_ignore_spec()

    def _load_ignore_spec(self) -> pathspec.PathSpec:
        patterns = list(_DEFAULT_IGNORES)
        gitignore = self.root / ".gitignore"
        if gitignore.is_file():
            try:
                patterns.extend(gitignore.read_text(encoding="utf-8").splitlines())
            except (OSError, UnicodeDecodeError):
                pass
        return pathspec.PathSpec.from_lines("gitwildmatch", patterns)

    def resolve(self, relative: str = ".") -> Path:
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("PATH_OUTSIDE_WORKSPACE") from exc
        return candidate

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix() or "."

    def is_hidden(self, path: Path) -> bool:
        try:
            relative = path.resolve().relative_to(self.root)
        except ValueError:
            return True
        return any(part.startswith(".") and part not in {".", ".."} for part in relative.parts)

    def is_ignored(self, path: Path) -> bool:
        relative = self.relative(path)
        if relative == ".":
            return False
        candidate = relative + ("/" if path.is_dir() else "")
        return self._ignore_spec.match_file(candidate)

    def include(self, path: Path, *, include_hidden: bool, include_ignored: bool) -> bool:
        if not include_hidden and self.is_hidden(path):
            return False
        if not include_ignored and self.is_ignored(path):
            return False
        return True

    @staticmethod
    def match_any(relative_path: str, patterns: list[str] | tuple[str, ...]) -> bool:
        if not patterns:
            return False
        posix = PurePosixPath(relative_path)
        return any(posix.match(pattern) or fnmatch(relative_path, pattern) for pattern in patterns)

    @staticmethod
    def encode_cursor(name: str) -> str:
        payload = json.dumps({"last": name}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def decode_cursor(cursor: str | None) -> str | None:
        if not cursor:
            return None
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            payload = base64.urlsafe_b64decode(padded.encode("ascii"))
            data = json.loads(payload.decode("utf-8"))
            last = data.get("last")
            if not isinstance(last, str):
                raise ValueError
            return last
        except Exception as exc:
            raise ValueError("INVALID_CURSOR") from exc
