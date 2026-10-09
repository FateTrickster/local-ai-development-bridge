from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_DEFAULT_BACKUPS = 3


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
class JsonlRotationPolicy:
    max_bytes: int = _DEFAULT_MAX_BYTES
    backup_count: int = _DEFAULT_BACKUPS

    @classmethod
    def from_env(cls) -> "JsonlRotationPolicy":
        return cls(
            max_bytes=_env_int("BRIDGE_LOG_MAX_BYTES", _DEFAULT_MAX_BYTES, 64 * 1024, 100 * 1024 * 1024),
            backup_count=_env_int("BRIDGE_LOG_BACKUPS", _DEFAULT_BACKUPS, 0, 10),
        )


def rotated_paths(path: Path, policy: JsonlRotationPolicy) -> list[Path]:
    """Return existing JSONL segments in chronological order."""
    output: list[Path] = []
    for index in range(policy.backup_count, 0, -1):
        candidate = path.with_name(path.name + f".{index}")
        if candidate.is_file():
            output.append(candidate)
    if path.is_file():
        output.append(path)
    return output


def rotate_before_append(path: Path, incoming_bytes: int, policy: JsonlRotationPolicy) -> bool:
    """Rotate `path` before an append would exceed the configured bound.

    Backups are stored as `<name>.1`, `<name>.2`, ... with `.1` being the most
    recent previous file. Rotation is expected to be called while the owning
    service lock is held.
    """
    if policy.max_bytes <= 0 or not path.is_file():
        return False
    try:
        current_size = path.stat().st_size
    except OSError:
        return False
    if current_size + max(0, int(incoming_bytes)) <= policy.max_bytes:
        return False

    if policy.backup_count <= 0:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return True

    oldest = path.with_name(path.name + f".{policy.backup_count}")
    try:
        oldest.unlink()
    except FileNotFoundError:
        pass

    for index in range(policy.backup_count - 1, 0, -1):
        source = path.with_name(path.name + f".{index}")
        target = path.with_name(path.name + f".{index + 1}")
        if source.exists():
            source.replace(target)

    if path.exists():
        path.replace(path.with_name(path.name + ".1"))
    return True
