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


def _env_list(name: str) -> list[str]:
    """Parse a comma-separated environment variable into a trimmed list."""
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    workspace_root: Path
    allow_write: bool
    allow_commands: bool
    max_read_bytes: int
    max_search_results: int
    max_search_bytes: int
    max_directory_entries: int
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 8000
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    dashboard_enabled: bool = True
    dashboard_port: int = 8766
    confirm_writes: bool = False
    confirm_commands: bool = False
    approval_ttl_seconds: int = 300
    desktop_notifications_enabled: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        default_root = Path.cwd().parent
        bridge_host = os.environ.get("BRIDGE_HOST", "127.0.0.1")
        bridge_port = _env_int("BRIDGE_PORT", 8000, 1, 65_535)
        return cls(
            workspace_root=Path(os.environ.get("WORKSPACE_ROOT", default_root)).resolve(),
            allow_write=_env_bool("ALLOW_WRITE", False),
            allow_commands=_env_bool("ALLOW_COMMANDS", False),
            max_read_bytes=_env_int("MAX_READ_BYTES", 262_144, 4_096, 8_388_608),
            max_search_results=_env_int("MAX_SEARCH_RESULTS", 500, 1, 10_000),
            max_search_bytes=_env_int("MAX_SEARCH_BYTES", 262_144, 4_096, 8_388_608),
            max_directory_entries=_env_int("MAX_DIRECTORY_ENTRIES", 500, 1, 10_000),
            bridge_host=bridge_host,
            bridge_port=bridge_port,
            allowed_hosts=tuple(_env_list("BRIDGE_ALLOWED_HOSTS")),
            allowed_origins=tuple(_env_list("BRIDGE_ALLOWED_ORIGINS")),
            dashboard_enabled=_env_bool("BRIDGE_DASHBOARD_ENABLED", True),
            dashboard_port=_env_int("BRIDGE_DASHBOARD_PORT", 8766, 1024, 65_535),
            confirm_writes=_env_bool("BRIDGE_CONFIRM_WRITES", False),
            confirm_commands=_env_bool("BRIDGE_CONFIRM_COMMANDS", False),
            approval_ttl_seconds=_env_int("BRIDGE_APPROVAL_TTL_SECONDS", 300, 30, 3600),
            desktop_notifications_enabled=_env_bool("BRIDGE_DESKTOP_NOTIFICATIONS", True),
        )