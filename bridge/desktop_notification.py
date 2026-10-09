from __future__ import annotations

import base64
import hashlib
import json
import platform
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_RUNTIME_DIR = _PACKAGE_ROOT / ".runtime"
_NOTIFICATION_STATE = _RUNTIME_DIR / "desktop-notification-state.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _format_duration(milliseconds: Any) -> str | None:
    try:
        total_seconds = max(0, int(milliseconds) // 1000)
    except (TypeError, ValueError):
        return None
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours} 小时 {minutes:02d} 分 {seconds:02d} 秒"
    if minutes:
        return f"{minutes} 分 {seconds:02d} 秒"
    return f"{seconds} 秒"


class DesktopNotificationService:
    """Program-level desktop notification for completed task plans.

    Completion detection and de-duplication live in the Bridge, not in prompts.
    A notification is emitted once for each distinct fully-completed task plan.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        state_file: Path = _NOTIFICATION_STATE,
        backend: Callable[[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.state_file = state_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._backend = backend or self._launch_native_notification
        self._lock = threading.RLock()

    @staticmethod
    def _completion_signature(todos: list[dict[str, Any]]) -> str:
        stable = [
            {
                "id": str(item.get("id") or ""),
                "content": str(item.get("content") or ""),
                "parent_id": item.get("parent_id"),
                "completed_at": item.get("completed_at"),
            }
            for item in todos
        ]
        stable.sort(key=lambda item: item["id"])
        payload = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_file.is_file():
            return {}
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save_state(self, payload: dict[str, Any]) -> None:
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.state_file)

    @staticmethod
    def _summary(task_state: dict[str, Any], todos: list[dict[str, Any]]) -> tuple[str, str]:
        roots = [item for item in todos if not item.get("parent_id")]
        if len(roots) == 1:
            task_name = str(roots[0].get("content") or "全部任务")
            duration = _format_duration(roots[0].get("elapsed_ms"))
        elif roots:
            task_name = f"{len(roots)} 个总任务"
            durations = [item.get("elapsed_ms") for item in roots if item.get("elapsed_ms") is not None]
            duration = _format_duration(max(durations)) if durations else None
        else:
            task_name = "全部任务"
            durations = [item.get("elapsed_ms") for item in todos if item.get("elapsed_ms") is not None]
            duration = _format_duration(max(durations)) if durations else None

        title = "Local AI Bridge · 任务已完成"
        lines = ["所有任务已经完成。", f"任务：{task_name}", f"完成项：{len(todos)} 个"]
        if duration:
            lines.append(f"总用时：{duration}")
        updated_at = task_state.get("updated_at")
        if updated_at:
            lines.append(f"完成时间：{updated_at}")
        return title, "\n".join(lines)

    @staticmethod
    def _launch_native_notification(title: str, message: str) -> dict[str, Any]:
        system = platform.system()
        if system == "Windows":
            def ps_quote(value: str) -> str:
                return "'" + value.replace("'", "''") + "'"

            script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                f"[System.Windows.Forms.MessageBox]::Show({ps_quote(message)}, {ps_quote(title)}, "
                "[System.Windows.Forms.MessageBoxButtons]::OK, "
                "[System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null"
            )
            encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
            creationflags = 0
            for name in ("CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
                creationflags |= int(getattr(subprocess, name, 0))
            process = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-STA",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-EncodedCommand",
                    encoded,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                close_fds=True,
            )
            return {"launched": True, "backend": "windows_message_box", "pid": process.pid}

        if system == "Darwin":
            process = subprocess.Popen(
                ["osascript", "-e", f'display notification {json.dumps(message)} with title {json.dumps(title)}'],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {"launched": True, "backend": "macos_notification", "pid": process.pid}

        if system == "Linux":
            try:
                process = subprocess.Popen(
                    ["notify-send", title, message],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except FileNotFoundError:
                return {"launched": False, "backend": "linux_notify_send", "reason": "NOTIFY_SEND_NOT_FOUND"}
            return {"launched": True, "backend": "linux_notify_send", "pid": process.pid}

        return {"launched": False, "backend": "unsupported", "reason": f"UNSUPPORTED_PLATFORM:{system}"}

    def evaluate_task_completion(self, task_state: dict[str, Any]) -> dict[str, Any]:
        todos = [item for item in task_state.get("todos", []) if isinstance(item, dict)]
        if not todos:
            return {"notified": False, "reason": "NO_TASKS"}
        if any(item.get("status") != "completed" for item in todos):
            return {"notified": False, "reason": "TASKS_STILL_ACTIVE"}
        if not self.enabled:
            return {"notified": False, "reason": "DESKTOP_NOTIFICATIONS_DISABLED"}

        signature = self._completion_signature(todos)
        with self._lock:
            state = self._load_state()
            if state.get("last_completion_signature") == signature:
                return {"notified": False, "reason": "ALREADY_NOTIFIED", "signature": signature}

            title, message = self._summary(task_state, todos)
            try:
                backend_result = self._backend(title, message)
            except Exception as exc:
                return {
                    "notified": False,
                    "reason": "NOTIFICATION_BACKEND_FAILED",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:400],
                }
            if not backend_result.get("launched"):
                return {
                    "notified": False,
                    "reason": backend_result.get("reason") or "NOTIFICATION_NOT_LAUNCHED",
                    "backend": backend_result.get("backend"),
                }

            notified_at = _now_iso()
            self._save_state(
                {
                    "last_completion_signature": signature,
                    "last_notified_at": notified_at,
                    "last_task_version": task_state.get("version"),
                    "backend": backend_result.get("backend"),
                }
            )
            return {
                "notified": True,
                "reason": "TASKS_COMPLETED",
                "signature": signature,
                "notified_at": notified_at,
                "backend": backend_result.get("backend"),
                "pid": backend_result.get("pid"),
            }

    def test_popup(self) -> dict[str, Any]:
        if not self.enabled:
            return {"notified": False, "reason": "DESKTOP_NOTIFICATIONS_DISABLED"}
        result = self._backend(
            "Local AI Bridge · 弹窗测试",
            "如果你看到这个窗口，说明任务完成后的本机弹窗提醒可以正常工作。",
        )
        return {"notified": bool(result.get("launched")), **result}
