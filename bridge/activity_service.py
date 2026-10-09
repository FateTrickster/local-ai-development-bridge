from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_RUNTIME_DIR = _PACKAGE_ROOT / ".runtime"
_ACTIVITY_FILE = _RUNTIME_DIR / "activity.jsonl"

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(token|secret|password|passwd|api[_-]?key)\s*([:=])\s*([^\s,;\x1b]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s]+")
_AUTHORIZATION = re.compile(r"(?i)authorization\s*:\s*[^\r\n]+")
_CAPABILITY_PATH = re.compile(r"/([A-Za-z0-9_-]{40,80})/mcp\b")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def redact_text(value: str, limit: int | None = None) -> str:
    text = str(value)
    text = _AUTHORIZATION.sub("Authorization: [REDACTED]", text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    text = _CAPABILITY_PATH.sub("/[REDACTED]/mcp", text)
    if limit is not None and limit >= 0 and len(text) > limit:
        return text[:limit] + "…"
    return text


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key)
            if normalized_key.casefold() in {
                "token",
                "secret",
                "password",
                "passwd",
                "authorization",
                "api_key",
                "apikey",
            }:
                output[normalized_key] = "[REDACTED]"
            else:
                output[normalized_key] = redact_value(item)
        return output
    if isinstance(value, (list, tuple, set)):
        return [redact_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


class ActivityService:
    def __init__(self, path: Path = _ACTIVITY_FILE):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._seq = self._load_last_seq()
        self._started: dict[str, float] = {}

    def _load_last_seq(self) -> int:
        if not self.path.is_file():
            return 0
        last_seq = 0
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    try:
                        last_seq = max(last_seq, int(record.get("seq", 0)))
                    except (TypeError, ValueError):
                        continue
        except OSError:
            return 0
        return last_seq

    def emit(
        self,
        event_type: str,
        *,
        status: str,
        title: str,
        component: str | None = None,
        tool: str | None = None,
        activity_id: str | None = None,
        details: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            record: dict[str, Any] = {
                "seq": self._seq,
                "activity_id": activity_id or str(uuid.uuid4()),
                "timestamp": _now_iso(),
                "type": str(event_type),
                "status": str(status),
                "title": redact_text(title, 400),
            }
            if component:
                record["component"] = redact_text(component, 120)
            if tool:
                record["tool"] = redact_text(tool, 120)
            if details:
                record["details"] = redact_value(details)
            if duration_ms is not None:
                record["duration_ms"] = max(0, int(duration_ms))
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            return record

    def start(
        self,
        tool: str,
        title: str,
        details: dict[str, Any] | None = None,
        *,
        component: str = "mcp",
    ) -> str:
        activity_id = str(uuid.uuid4())
        with self._lock:
            self._started[activity_id] = time.perf_counter()
        self.emit(
            "tool_started",
            status="running",
            title=title,
            component=component,
            tool=tool,
            activity_id=activity_id,
            details=details,
        )
        return activity_id

    def finish(
        self,
        activity_id: str,
        *,
        tool: str,
        title: str,
        status: str,
        event_type: str | None = None,
        details: dict[str, Any] | None = None,
        component: str = "mcp",
    ) -> dict[str, Any]:
        with self._lock:
            started = self._started.pop(activity_id, None)
        duration_ms = None if started is None else round((time.perf_counter() - started) * 1000)
        return self.emit(
            event_type or ("tool_completed" if status == "completed" else "tool_failed"),
            status=status,
            title=title,
            component=component,
            tool=tool,
            activity_id=activity_id,
            details=details,
            duration_ms=duration_ms,
        )

    def list_events(self, limit: int = 100, after_seq: int | None = None) -> dict[str, Any]:
        limit = max(1, min(int(limit), 500))
        after = max(0, int(after_seq or 0))
        events: list[dict[str, Any]] = []
        truncated = False
        if not self.path.is_file():
            return {"events": [], "next_seq": after, "truncated": False}
        try:
            if after > 0:
                with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        try:
                            item = json.loads(line)
                            seq = int(item.get("seq", 0))
                        except (json.JSONDecodeError, TypeError, ValueError):
                            continue
                        if seq <= after:
                            continue
                        if len(events) >= limit:
                            truncated = True
                            break
                        events.append(item)
            else:
                tail: deque[dict[str, Any]] = deque(maxlen=limit)
                total_valid = 0
                with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        try:
                            item = json.loads(line)
                            int(item.get("seq", 0))
                        except (json.JSONDecodeError, TypeError, ValueError):
                            continue
                        total_valid += 1
                        tail.append(item)
                events = list(tail)
                truncated = total_valid > len(events)
        except OSError:
            return {"events": [], "next_seq": after, "truncated": False}
        next_seq = int(events[-1].get("seq", after)) if events else after
        return {"events": events, "next_seq": next_seq, "truncated": truncated}
