from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class Settings:
    workspace_root: Path
    allow_write: bool
    allow_commands: bool
    max_read_bytes: int
    max_search_results: int
    max_search_bytes: int
    max_directory_entries: int

    @classmethod
    def from_env(cls) -> "Settings":
        default_root = Path.cwd().parent
        return cls(
            workspace_root=Path(os.environ.get("WORKSPACE_ROOT", default_root)).resolve(),
            allow_write=_env_bool("ALLOW_WRITE", False),
            allow_commands=_env_bool("ALLOW_COMMANDS", False),
            max_read_bytes=_env_int("MAX_READ_BYTES", 262_144, 4_096, 8_388_608),
            max_search_results=_env_int("MAX_SEARCH_RESULTS", 500, 1, 10_000),
            max_search_bytes=_env_int("MAX_SEARCH_BYTES", 262_144, 4_096, 8_388_608),
            max_directory_entries=_env_int("MAX_DIRECTORY_ENTRIES", 500, 1, 10_000),
        )
