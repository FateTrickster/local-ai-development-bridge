from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .jsonl_utils import JsonlRotationPolicy, rotate_before_append, rotated_paths


_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_RUNTIME_DIR = _PACKAGE_ROOT / ".runtime"
_AI_OUTPUT_FILE = _RUNTIME_DIR / "ai-output.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class AITelemetryService:
    """Stores token-count telemetry supplied by an automatic client adapter.

    The Bridge cannot infer ChatGPT assistant token streaming from MCP traffic.
    Therefore this service never estimates tokens from tool payloads or prompts.
    A compatible local client may POST exact output_tokens + duration_ms; when no
    reporter is connected, the dashboard reports an explicit unavailable state.
    """

    def __init__(
        self,
        path: Path = _AI_OUTPUT_FILE,
        rotation: JsonlRotationPolicy | None = None,
    ):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rotation = rotation or JsonlRotationPolicy.from_env()
        self._lock = threading.RLock()
        self._seq = self._load_last_seq()

    def _segments(self) -> list[Path]:
        return rotated_paths(self.path, self.rotation)

    def _load_last_seq(self) -> int:
        seq = 0
        for segment in self._segments():
            try:
                with segment.open("r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        try:
                            item = json.loads(line)
                            seq = max(seq, int(item.get("seq", 0)))
                        except (json.JSONDecodeError, TypeError, ValueError):
                            continue
            except OSError:
                continue
        return seq

    def record(
        self,
        *,
        output_tokens: int,
        duration_ms: int,
        source: str = "client",
        model: str | None = None,
    ) -> dict[str, Any]:
        output_tokens = int(output_tokens)
        duration_ms = int(duration_ms)
        if output_tokens < 0 or output_tokens > 10_000_000:
            raise ValueError("INVALID_OUTPUT_TOKENS")
        if duration_ms < 1 or duration_ms > 86_400_000:
            raise ValueError("INVALID_OUTPUT_DURATION")
        source = str(source).strip()[:120] or "client"
        model = str(model).strip()[:120] if model else None
        with self._lock:
            self._seq += 1
            record = {
                "seq": self._seq,
                "created_at": _now_iso(),
                "output_tokens": output_tokens,
                "duration_ms": duration_ms,
                "tps": round(output_tokens / (duration_ms / 1000), 2),
                "source": source,
            }
            if model:
                record["model"] = model
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            rotate_before_append(self.path, len(line.encode("utf-8")), self.rotation)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
            return record

    def _recent(self, seconds: int, now: float) -> list[dict[str, Any]]:
        cutoff = now - max(1, seconds)
        tail: deque[dict[str, Any]] = deque(maxlen=5000)
        for segment in self._segments():
            try:
                with segment.open("r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        created = _parse_epoch(item.get("created_at"))
                        if created is not None and created >= cutoff:
                            tail.append(item)
            except OSError:
                continue
        return list(tail)

    @staticmethod
    def _window(events: list[dict[str, Any]]) -> dict[str, Any]:
        tokens = sum(max(0, int(item.get("output_tokens", 0) or 0)) for item in events)
        duration_ms = sum(max(0, int(item.get("duration_ms", 0) or 0)) for item in events)
        return {
            "output_tokens": tokens,
            "events": len(events),
            "avg_tps": round(tokens / (duration_ms / 1000), 2) if duration_ms > 0 else None,
        }

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        events_5m = self._recent(300, now)
        events_1m = [
            item for item in events_5m
            if (_parse_epoch(item.get("created_at")) or 0) >= now - 60
        ]
        latest = events_5m[-1] if events_5m else None
        latest_epoch = _parse_epoch(latest.get("created_at")) if latest else None
        age_seconds = max(0.0, now - latest_epoch) if latest_epoch is not None else None
        return {
            "available": latest is not None,
            "state": "live" if age_seconds is not None and age_seconds <= 30 else ("stale" if latest else "not_connected"),
            "source": latest.get("source") if latest else None,
            "model": latest.get("model") if latest else None,
            "latest_at": latest.get("created_at") if latest else None,
            "latest_tps": latest.get("tps") if latest else None,
            "one_minute": self._window(events_1m),
            "five_minutes": self._window(events_5m),
            "note": None if latest else "Assistant token stream telemetry is not available from MCP; Bridge does not fabricate TPS.",
        }
