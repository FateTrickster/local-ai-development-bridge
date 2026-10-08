from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_RUNTIME_DIR = _PACKAGE_ROOT / ".runtime"
_TODO_FILE = _RUNTIME_DIR / "todos.json"
_PROGRESS_FILE = _RUNTIME_DIR / "progress.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class TaskService:
    def __init__(self, todo_file: Path = _TODO_FILE, progress_file: Path = _PROGRESS_FILE):
        self.todo_file = todo_file
        self.progress_file = progress_file
        self.todo_file.parent.mkdir(parents=True, exist_ok=True)
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

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
            progress_count = 0
            if self.progress_file.is_file():
                try:
                    with self.progress_file.open("r", encoding="utf-8") as handle:
                        progress_count = sum(1 for _ in handle)
                except OSError:
                    progress_count = 0
            return {**snapshot, "progress_events": progress_count}

    def set_todos(self, todos: list[dict[str, Any]]) -> dict[str, Any]:
        if len(todos) > 24:
            raise ValueError("TOO_MANY_TODOS")
        ids: set[str] = set()
        normalized: list[dict[str, str]] = []
        in_progress = 0
        for raw in todos:
            todo_id = str(raw.get("id", "")).strip()
            content = str(raw.get("content", "")).strip()
            status = str(raw.get("status", "")).strip()
            if not todo_id or len(todo_id) > 80:
                raise ValueError("INVALID_TODO_ID")
            if todo_id in ids:
                raise ValueError(f"DUPLICATE_TODO_ID: {todo_id}")
            if not content or len(content) > 400:
                raise ValueError(f"INVALID_TODO_CONTENT: {todo_id}")
            if status not in {"pending", "in_progress", "completed"}:
                raise ValueError(f"INVALID_TODO_STATUS: {todo_id}")
            if status == "in_progress":
                in_progress += 1
            ids.add(todo_id)
            normalized.append({"id": todo_id, "content": content, "status": status})
        if in_progress > 1:
            raise ValueError("MULTIPLE_IN_PROGRESS_TODOS")

        with self._lock:
            previous = self._load_snapshot()
            snapshot = {
                "version": int(previous.get("version", 0)) + 1,
                "todos": normalized,
                "updated_at": _now_iso(),
            }
            temporary = self.todo_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.todo_file)
            return snapshot

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
                active = [
                    item["id"]
                    for item in snapshot.get("todos", [])
                    if isinstance(item, dict) and item.get("status") == "in_progress"
                ]
                if len(active) == 1:
                    todo_id = active[0]
            elif todo_id not in todo_ids:
                raise ValueError(f"UNKNOWN_TODO_ID: {todo_id}")

            seq = 1
            if self.progress_file.is_file():
                try:
                    with self.progress_file.open("r", encoding="utf-8") as handle:
                        seq += sum(1 for _ in handle)
                except OSError:
                    pass
            event: dict[str, Any] = {
                "seq": seq,
                "todo_id": todo_id,
                "message": message,
                "created_at": _now_iso(),
            }
            if current is not None:
                event.update({"current": current, "total": total})
            if phase is not None:
                event.update({"phase": phase, "phase_total": phase_total})
            with self.progress_file.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            return event
