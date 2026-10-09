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

from .jsonl_utils import JsonlRotationPolicy, rotate_before_append, rotated_paths


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
    def __init__(
        self,
        path: Path = _ACTIVITY_FILE,
        rotation: JsonlRotationPolicy | None = None,
    ):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rotation = rotation or JsonlRotationPolicy.from_env()
        self._lock = threading.RLock()
        self._seq = self._load_last_seq()
        self._started: dict[str, float] = {}

    def _segments(self) -> list[Path]:
        return rotated_paths(self.path, self.rotation)

    def _earliest_retained_seq(self) -> int | None:
        for segment in self._segments():
            try:
                with segment.open("r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        try:
                            item = json.loads(line)
                            return int(item.get("seq", 0))
                        except (json.JSONDecodeError, TypeError, ValueError):
                            continue
            except OSError:
                continue
        return None

    def _load_last_seq(self) -> int:
        last_seq = 0
        for segment in self._segments():
            try:
                with segment.open("r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        try:
                            record = json.loads(line)
                            last_seq = max(last_seq, int(record.get("seq", 0)))
                        except (json.JSONDecodeError, TypeError, ValueError):
                            continue
            except OSError:
                continue
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
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            rotate_before_append(self.path, len(line.encode("utf-8")), self.rotation)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
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
        segments = self._segments()
        if not segments:
            return {
                "events": [],
                "next_seq": after,
                "truncated": False,
                "earliest_seq": None,
                "history_lost": False,
            }
        earliest_seq = self._earliest_retained_seq()
        history_lost = bool(after > 0 and earliest_seq is not None and earliest_seq > after + 1)
        try:
            if after > 0:
                for segment in segments:
                    with segment.open("r", encoding="utf-8", errors="replace") as handle:
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
                    if truncated:
                        break
            else:
                tail: deque[dict[str, Any]] = deque(maxlen=limit)
                total_valid = 0
                for segment in segments:
                    with segment.open("r", encoding="utf-8", errors="replace") as handle:
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
            return {
                "events": [],
                "next_seq": after,
                "truncated": False,
                "earliest_seq": earliest_seq,
                "history_lost": history_lost,
            }
        next_seq = int(events[-1].get("seq", after)) if events else after
        return {
            "events": events,
            "next_seq": next_seq,
            "truncated": truncated,
            "earliest_seq": earliest_seq,
            "history_lost": history_lost,
        }

    def activity_metrics(self) -> dict[str, Any]:
        """Summarize recent observable MCP activity for the focused Dashboard.

        Only ``tool_started`` events from the MCP component count as operations,
        so start/completion pairs are not double-counted. The latest heartbeat
        may come from any structured event, such as a background command
        completion. This intentionally does not claim to observe model-internal
        reasoning.
        """
        now = time.time()
        payload = self.list_events(limit=500)
        events = payload.get("events", []) if isinstance(payload, dict) else []

        def epoch(item: dict[str, Any]) -> float | None:
            raw = item.get("timestamp")
            if not isinstance(raw, str) or not raw:
                return None
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return None

        parsed: list[tuple[dict[str, Any], float]] = []
        for item in events:
            if not isinstance(item, dict):
                continue
            stamp = epoch(item)
            if stamp is not None:
                parsed.append((item, stamp))

        operations = [
            stamp
            for item, stamp in parsed
            if item.get("type") == "tool_started" and item.get("component", "mcp") == "mcp"
        ]
        one_minute = sum(1 for stamp in operations if stamp >= now - 60)
        five_minutes = sum(1 for stamp in operations if stamp >= now - 300)

        latest_item: dict[str, Any] | None = None
        latest_stamp: float | None = None
        if parsed:
            latest_item, latest_stamp = max(
                parsed,
                key=lambda pair: (int(pair[0].get("seq", 0) or 0), pair[1]),
            )

        age_seconds = max(0.0, now - latest_stamp) if latest_stamp is not None else None
        if age_seconds is None:
            state = "no_activity"
        elif age_seconds <= 15:
            state = "active"
        elif age_seconds <= 60:
            state = "recent"
        else:
            state = "idle"

        return {
            "available": latest_item is not None,
            "state": state,
            "one_minute_operations": one_minute,
            "five_minute_operations": five_minutes,
            "five_minute_average_per_minute": round(five_minutes / 5, 1),
            "latest_at": latest_item.get("timestamp") if latest_item else None,
            "latest_age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
            "latest_type": latest_item.get("type") if latest_item else None,
            "latest_tool": latest_item.get("tool") if latest_item else None,
            "latest_component": latest_item.get("component") if latest_item else None,
            "latest_title": latest_item.get("title") if latest_item else None,
            "note": "Counts observable MCP operations only; model-internal reasoning is not visible to the Bridge.",
        }
