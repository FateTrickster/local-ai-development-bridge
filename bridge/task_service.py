from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .jsonl_utils import JsonlRotationPolicy, rotate_before_append, rotated_paths


_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_RUNTIME_DIR = _PACKAGE_ROOT / ".runtime"
_TODO_FILE = _RUNTIME_DIR / "todos.json"
_PROGRESS_FILE = _RUNTIME_DIR / "progress.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _elapsed_ms(started_at: Any, completed_at: Any = None) -> int | None:
    started = _parse_iso(started_at)
    if started is None:
        return None
    completed = _parse_iso(completed_at) or datetime.now(timezone.utc)
    return max(0, round((completed - started).total_seconds() * 1000))


class TaskService:
    def __init__(
        self,
        todo_file: Path = _TODO_FILE,
        progress_file: Path = _PROGRESS_FILE,
        rotation: JsonlRotationPolicy | None = None,
    ):
        self.todo_file = todo_file
        self.progress_file = progress_file
        self.rotation = rotation or JsonlRotationPolicy.from_env()
        self.todo_file.parent.mkdir(parents=True, exist_ok=True)
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._progress_seq = self._load_progress_seq()

    def _segments(self) -> list[Path]:
        return rotated_paths(self.progress_file, self.rotation)

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

    def _load_progress_seq(self) -> int:
        last_seq = 0
        for segment in self._segments():
            try:
                with segment.open("r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        try:
                            event = json.loads(line)
                            last_seq = max(last_seq, int(event.get("seq", 0)))
                        except (json.JSONDecodeError, TypeError, ValueError):
                            continue
            except OSError:
                continue
        return last_seq

    def _load_snapshot(self) -> dict[str, Any]:
        if not self.todo_file.is_file():
            return {"version": 0, "todos": [], "updated_at": None}
        try:
            data = json.loads(self.todo_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 0, "todos": [], "updated_at": None}
        if not isinstance(data, dict):
            return {"version": 0, "todos": [], "updated_at": None}
        return data

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            snapshot = self._load_snapshot()
            raw_todos = snapshot.get("todos", []) if isinstance(snapshot.get("todos"), list) else []
            by_id = {
                str(item.get("id")): item
                for item in raw_todos
                if isinstance(item, dict) and item.get("id")
            }

            def depth(todo_id: str) -> int:
                seen: set[str] = set()
                current = by_id.get(todo_id)
                value = 0
                while isinstance(current, dict) and current.get("parent_id"):
                    parent_id = str(current.get("parent_id"))
                    if parent_id in seen or parent_id not in by_id:
                        break
                    seen.add(parent_id)
                    value += 1
                    current = by_id[parent_id]
                return value

            enriched: list[dict[str, Any]] = []
            for item in raw_todos:
                if not isinstance(item, dict):
                    continue
                current = dict(item)
                current["level"] = depth(str(current.get("id", "")))
                current["elapsed_ms"] = _elapsed_ms(current.get("started_at"), current.get("completed_at"))
                enriched.append(current)

            in_progress = [item for item in enriched if item.get("status") == "in_progress"]
            active_leaf = max(in_progress, key=lambda item: int(item.get("level", 0)), default=None)
            active_path: list[str] = []
            if active_leaf is not None:
                cursor: dict[str, Any] | None = active_leaf
                while cursor is not None:
                    active_path.append(str(cursor.get("id")))
                    parent_id = cursor.get("parent_id")
                    cursor = by_id.get(str(parent_id)) if parent_id else None
                active_path.reverse()

            progress_count = 0
            for segment in self._segments():
                try:
                    with segment.open("r", encoding="utf-8", errors="replace") as handle:
                        progress_count += sum(1 for line in handle if line.strip())
                except OSError:
                    continue
            return {
                **snapshot,
                "todos": enriched,
                "active_leaf_id": active_leaf.get("id") if active_leaf else None,
                "active_path": active_path,
                "progress_events": progress_count,
            }

    def get_progress_events(self, limit: int = 100, after_seq: int | None = None) -> dict[str, Any]:
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
                                event = json.loads(line)
                                seq = int(event.get("seq", 0))
                            except (json.JSONDecodeError, TypeError, ValueError):
                                continue
                            if seq <= after:
                                continue
                            if len(events) >= limit:
                                truncated = True
                                break
                            events.append(event)
                    if truncated:
                        break
            else:
                tail: deque[dict[str, Any]] = deque(maxlen=limit)
                total_valid = 0
                for segment in segments:
                    with segment.open("r", encoding="utf-8", errors="replace") as handle:
                        for line in handle:
                            try:
                                event = json.loads(line)
                                int(event.get("seq", 0))
                            except (json.JSONDecodeError, TypeError, ValueError):
                                continue
                            total_valid += 1
                            tail.append(event)
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

    def set_todos(self, todos: list[dict[str, Any]]) -> dict[str, Any]:
        if len(todos) > 64:
            raise ValueError("TOO_MANY_TODOS")
        ids: set[str] = set()
        normalized: list[dict[str, Any]] = []
        for raw in todos:
            todo_id = str(raw.get("id", "")).strip()
            content = str(raw.get("content", "")).strip()
            status = str(raw.get("status", "")).strip()
            raw_parent = raw.get("parent_id")
            parent_id = str(raw_parent).strip() if raw_parent is not None else None
            if parent_id == "":
                parent_id = None
            if not todo_id or len(todo_id) > 80:
                raise ValueError("INVALID_TODO_ID")
            if todo_id in ids:
                raise ValueError(f"DUPLICATE_TODO_ID: {todo_id}")
            if not content or len(content) > 400:
                raise ValueError(f"INVALID_TODO_CONTENT: {todo_id}")
            if status not in {"pending", "in_progress", "completed"}:
                raise ValueError(f"INVALID_TODO_STATUS: {todo_id}")
            if parent_id is not None and len(parent_id) > 80:
                raise ValueError(f"INVALID_PARENT_ID: {todo_id}")
            ids.add(todo_id)
            normalized.append({
                "id": todo_id,
                "content": content,
                "status": status,
                "parent_id": parent_id,
            })

        by_id = {item["id"]: item for item in normalized}
        for item in normalized:
            parent_id = item.get("parent_id")
            if parent_id is not None and parent_id not in by_id:
                raise ValueError(f"UNKNOWN_PARENT_ID: {item['id']} -> {parent_id}")
            if parent_id == item["id"]:
                raise ValueError(f"TODO_PARENT_CYCLE: {item['id']}")

        def ancestors(todo_id: str) -> list[str]:
            result: list[str] = []
            seen: set[str] = set()
            cursor = by_id[todo_id]
            while cursor.get("parent_id") is not None:
                parent_id = str(cursor["parent_id"])
                if parent_id in seen:
                    raise ValueError(f"TODO_PARENT_CYCLE: {todo_id}")
                seen.add(parent_id)
                result.append(parent_id)
                cursor = by_id[parent_id]
            return result

        depths: dict[str, int] = {}
        for item in normalized:
            depths[item["id"]] = len(ancestors(item["id"]))

        in_progress_ids = [item["id"] for item in normalized if item["status"] == "in_progress"]
        active_leaf_id: str | None = None
        if in_progress_ids:
            active_leaf_id = max(in_progress_ids, key=lambda todo_id: depths[todo_id])
            active_chain = set(ancestors(active_leaf_id)) | {active_leaf_id}
            if any(todo_id not in active_chain for todo_id in in_progress_ids):
                raise ValueError("MULTIPLE_IN_PROGRESS_TODOS")
            if any(by_id[todo_id]["status"] == "completed" for todo_id in ancestors(active_leaf_id)):
                raise ValueError("ACTIVE_CHILD_OF_COMPLETED_TASK")

        now = _now_iso()
        with self._lock:
            previous = self._load_snapshot()
            previous_by_id = {
                str(item.get("id")): item
                for item in previous.get("todos", [])
                if isinstance(item, dict) and item.get("id")
            }

            for item in normalized:
                old = previous_by_id.get(item["id"], {})
                created_at = old.get("created_at") or now
                started_at = old.get("started_at")
                completed_at = old.get("completed_at") if item["status"] == "completed" else None
                if item["status"] == "in_progress" and not started_at:
                    started_at = now
                if item["status"] == "completed":
                    started_at = started_at or old.get("created_at") or now
                    completed_at = completed_at or now
                item["created_at"] = created_at
                item["started_at"] = started_at
                item["completed_at"] = completed_at

            if active_leaf_id is not None:
                active_chain = list(reversed(ancestors(active_leaf_id))) + [active_leaf_id]
                for todo_id in active_chain:
                    item = by_id[todo_id]
                    if not item.get("started_at"):
                        item["started_at"] = now

            snapshot = {
                "version": int(previous.get("version", 0)) + 1,
                "todos": normalized,
                "updated_at": now,
            }
            temporary = self.todo_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.todo_file)
            return self.get_state()

    def report_progress(
        self,
        message: str,
        todo_id: str | None = None,
        current: int | None = None,
        total: int | None = None,
        phase: int | None = None,
        phase_total: int | None = None,
    ) -> dict[str, Any]:
        message = message.strip()
        if not message or len(message) > 2000:
            raise ValueError("INVALID_PROGRESS_MESSAGE")
        if (current is None) ^ (total is None):
            raise ValueError("CURRENT_AND_TOTAL_MUST_BE_PAIRED")
        if (phase is None) ^ (phase_total is None):
            raise ValueError("PHASE_AND_PHASE_TOTAL_MUST_BE_PAIRED")
        if current is not None and (current < 0 or total is None or total < 1 or current > total):
            raise ValueError("INVALID_PROGRESS_RANGE")
        if phase is not None and (phase < 1 or phase_total is None or phase_total < 1 or phase > phase_total):
            raise ValueError("INVALID_PHASE_RANGE")

        with self._lock:
            snapshot = self._load_snapshot()
            todo_ids = {item["id"] for item in snapshot.get("todos", []) if isinstance(item, dict) and "id" in item}
            if todo_id is None:
                todo_id = self.get_state().get("active_leaf_id")
            elif todo_id not in todo_ids:
                raise ValueError(f"UNKNOWN_TODO_ID: {todo_id}")

            self._progress_seq += 1
            event: dict[str, Any] = {
                "seq": self._progress_seq,
                "todo_id": todo_id,
                "message": message,
                "created_at": _now_iso(),
            }
            if current is not None:
                event.update({"current": current, "total": total})
            if phase is not None:
                event.update({"phase": phase, "phase_total": phase_total})
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            rotate_before_append(self.progress_file, len(line.encode("utf-8")), self.rotation)
            with self.progress_file.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
            return event
