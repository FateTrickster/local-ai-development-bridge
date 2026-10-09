from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .activity_service import redact_value


_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_RUNTIME_DIR = _PACKAGE_ROOT / ".runtime"
_STATE_FILE = _RUNTIME_DIR / "launcher-state.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class LauncherStateStore:
    """Small atomic state file shared by the launcher and local dashboard.

    The file intentionally stores only non-secret runtime metadata. All values are
    passed through the same redaction layer used by ActivityService before they
    reach disk.
    """

    def __init__(self, path: Path = _STATE_FILE):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def read(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.is_file():
                return {
                    "status": "not_managed",
                    "phase": "idle",
                    "updated_at": None,
                    "managed_by_launcher": False,
                }
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {
                    "status": "unknown",
                    "phase": "state_unreadable",
                    "updated_at": None,
                    "managed_by_launcher": False,
                }
            if not isinstance(payload, dict):
                return {
                    "status": "unknown",
                    "phase": "state_invalid",
                    "updated_at": None,
                    "managed_by_launcher": False,
                }
            return payload

    def replace(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            state = redact_value(dict(payload))
            if not isinstance(state, dict):
                state = {"value": state}
            state["updated_at"] = _now_iso()
            state.setdefault("managed_by_launcher", True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
            return state

    def update(self, **changes: Any) -> dict[str, Any]:
        with self._lock:
            current = self.read()
            current.update(changes)
            return self.replace(current)

    def mark_phase(self, phase: str, *, status: str = "starting", **details: Any) -> dict[str, Any]:
        return self.update(phase=phase, status=status, **details)
