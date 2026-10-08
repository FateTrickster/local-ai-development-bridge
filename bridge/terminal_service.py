from __future__ import annotations

import base64
import os
import platform
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .pathguard import WorkspaceGuard

if platform.system() == "Windows":
    from winpty import PtyProcess
else:
    PtyProcess = None  # type: ignore[assignment]


_MAX_BUFFER_BYTES = 4 * 1024 * 1024
_DEFAULT_READ_BYTES = 32 * 1024
_MAX_READ_BYTES = 128 * 1024


@dataclass
class CommandRecord:
    command_id: str
    command: str
    cwd: str
    process: Any
    started_at: float
    buffer: bytearray = field(default_factory=bytearray)
    earliest_offset: int = 0
    total_offset: int = 0
    status: str = "running"
    exit_code: int | None = None
    input_seq: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock)
    condition: threading.Condition = field(init=False)

    def __post_init__(self) -> None:
        self.condition = threading.Condition(self.lock)


class TerminalService:
    def __init__(self, settings: Settings, guard: WorkspaceGuard):
        self.settings = settings
        self.guard = guard
        self._commands: dict[str, CommandRecord] = {}
        self._registry_lock = threading.RLock()

    @staticmethod
    def _powershell_argv(command: str) -> list[str]:
        encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
        return [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ]

    @staticmethod
    def _shell_argv(command: str) -> list[str]:
        if platform.system() == "Windows":
            return TerminalService._powershell_argv(command)
        return ["/bin/bash", "-lc", command]

    def _require_commands(self) -> None:
        if not self.settings.allow_commands:
            raise PermissionError("COMMANDS_DISABLED")

    def _append_output(self, record: CommandRecord, text: str) -> None:
        data = text.encode("utf-8", errors="replace")
        with record.condition:
            record.buffer.extend(data)
            record.total_offset += len(data)
            if len(record.buffer) > _MAX_BUFFER_BYTES:
                overflow = len(record.buffer) - _MAX_BUFFER_BYTES
                del record.buffer[:overflow]
                record.earliest_offset += overflow
            record.condition.notify_all()

    def _finalize(self, record: CommandRecord, exit_code: int | None) -> None:
        with record.condition:
            record.exit_code = exit_code
            record.status = "completed" if exit_code in (0, None) else "failed"
            record.condition.notify_all()

    def _reader(self, record: CommandRecord) -> None:
        process = record.process
        try:
            while True:
                try:
                    chunk = process.read(4096)
                except EOFError:
                    break
                except Exception:
                    if not process.isalive():
                        break
                    time.sleep(0.02)
                    continue
                if chunk:
                    self._append_output(record, chunk)
                elif not process.isalive():
                    break
                else:
                    time.sleep(0.01)
        finally:
            exit_code: int | None = None
            try:
                exit_code = process.wait()
            except Exception:
                try:
                    exit_code = process.exitstatus
                except Exception:
                    exit_code = None
            try:
                process.close()
            except Exception:
                pass
            record.process = None
            self._finalize(record, exit_code)

    def _spawn(self, command: str, workdir: Path) -> Any:
        argv = self._shell_argv(command)
        if platform.system() == "Windows":
            if PtyProcess is None:
                raise RuntimeError("PTY_BACKEND_UNAVAILABLE")
            return PtyProcess.spawn(argv, cwd=str(workdir), env=os.environ.copy(), dimensions=(30, 120))
        raise RuntimeError("PTY_BACKEND_NOT_IMPLEMENTED_ON_THIS_PLATFORM")

    def _record(self, command_id: str) -> CommandRecord:
        with self._registry_lock:
            record = self._commands.get(command_id)
        if record is None:
            raise KeyError(f"UNKNOWN_COMMAND_ID: {command_id}")
        return record

    def _prune(self) -> None:
        with self._registry_lock:
            if len(self._commands) <= 64:
                return
            completed = sorted(
                (record for record in self._commands.values() if record.status != "running"),
                key=lambda record: record.started_at,
            )
            for record in completed[: max(0, len(self._commands) - 64)]:
                self._commands.pop(record.command_id, None)

    def run_command(
        self,
        command: str,
        cwd: str = ".",
        background: bool = False,
        timeout_ms: int = 120_000,
    ) -> dict[str, Any]:
        self._require_commands()
        if not command.strip():
            raise ValueError("EMPTY_COMMAND")
        workdir = self.guard.resolve(cwd)
        if not workdir.is_dir():
            raise ValueError(f"NOT_A_DIRECTORY: {cwd}")
        timeout_ms = max(1_000, min(timeout_ms, 120_000))
        command_id = str(uuid.uuid4())
        process = self._spawn(command, workdir)
        record = CommandRecord(
            command_id=command_id,
            command=command,
            cwd=self.guard.relative(workdir),
            process=process,
            started_at=time.time(),
        )
        with self._registry_lock:
            self._commands[command_id] = record
        threading.Thread(target=self._reader, args=(record,), daemon=True, name=f"terminal-{command_id[:8]}").start()
        self._prune()

        if not background:
            with record.condition:
                if record.status == "running":
                    record.condition.wait_for(lambda: record.status != "running", timeout=timeout_ms / 1000)
        result = self.get_command_output(command_id, offset=0, max_bytes=_DEFAULT_READ_BYTES)
        result.update({"command_id": command_id, "cwd": record.cwd, "background": background})
        return result

    def get_command_output(self, command_id: str, offset: int = 0, max_bytes: int = _DEFAULT_READ_BYTES) -> dict[str, Any]:
        record = self._record(command_id)
        max_bytes = max(1, min(max_bytes, _MAX_READ_BYTES))
        with record.lock:
            requested = max(0, offset)
            output_lost = requested < record.earliest_offset
            start = max(requested, record.earliest_offset)
            local_start = start - record.earliest_offset
            chunk = bytes(record.buffer[local_start : local_start + max_bytes])
            next_offset = start + len(chunk)
            return {
                "command_id": command_id,
                "status": record.status,
                "exit_code": record.exit_code,
                "cwd": record.cwd,
                "earliest_offset": record.earliest_offset,
                "next_offset": next_offset,
                "output_lost": output_lost,
                "input_seq": record.input_seq,
                "output": chunk.decode("utf-8", errors="replace"),
                "has_more": next_offset < record.total_offset,
            }

    def send_command_input(self, command_id: str, input: str, append_newline: bool = True) -> dict[str, Any]:
        self._require_commands()
        record = self._record(command_id)
        with record.condition:
            if record.status != "running":
                raise RuntimeError("COMMAND_NOT_RUNNING")
            payload = input + ("\r\n" if append_newline else "")
            record.process.write(payload)
            record.input_seq += 1
            seq = record.input_seq
            record.condition.notify_all()
        return {"command_id": command_id, "input_seq": seq, "status": record.status}

    def wait(self, command_id: str, timeout_ms: int = 30_000) -> dict[str, Any]:
        record = self._record(command_id)
        timeout_ms = max(1_000, min(timeout_ms, 120_000))
        with record.condition:
            before = record.total_offset
            if record.status == "running":
                record.condition.wait_for(
                    lambda: record.status != "running" or record.total_offset != before,
                    timeout=timeout_ms / 1000,
                )
            return {
                "command_id": command_id,
                "status": record.status,
                "exit_code": record.exit_code,
                "earliest_offset": record.earliest_offset,
                "next_offset": record.total_offset,
                "input_seq": record.input_seq,
            }

    def terminate(self, command_id: str, force: bool = False) -> dict[str, Any]:
        self._require_commands()
        record = self._record(command_id)
        with record.condition:
            if record.status == "running":
                try:
                    if force:
                        record.process.kill()
                    else:
                        record.process.terminate(force=False)
                finally:
                    record.condition.notify_all()
        return {"command_id": command_id, "status": record.status}
