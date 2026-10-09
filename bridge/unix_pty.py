from __future__ import annotations

import errno
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import time
from typing import Any


class UnixPtyProcess:
    """Small stdlib PTY adapter with the subset TerminalService needs.

    It intentionally mirrors the pywinpty surface (`read`, `write`, `isalive`,
    `wait`, `terminate`, `kill`, `close`, `exitstatus`) so TerminalService can use
    one lifecycle on Windows, Linux and macOS without an additional dependency.
    """

    def __init__(self, process: subprocess.Popen[bytes], master_fd: int):
        self.process = process
        self.master_fd = master_fd
        self.pid = process.pid
        self.closed = False
        self.exitstatus: int | None = None

    @classmethod
    def spawn(
        cls,
        argv: list[str],
        *,
        cwd: str,
        env: dict[str, str],
        dimensions: tuple[int, int] = (30, 120),
    ) -> "UnixPtyProcess":
        master_fd, slave_fd = pty.openpty()
        rows, columns = dimensions
        try:
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                close_fds=True,
            )
        except Exception:
            os.close(master_fd)
            os.close(slave_fd)
            raise
        finally:
            try:
                os.close(slave_fd)
            except OSError:
                pass
        return cls(process, master_fd)

    def read(self, size: int = 4096) -> str:
        if self.closed:
            raise EOFError("Pty is closed")
        ready, _, _ = select.select([self.master_fd], [], [], 0.1)
        if not ready:
            if not self.isalive():
                raise EOFError("Pty is closed")
            return ""
        try:
            data = os.read(self.master_fd, max(1, size))
        except OSError as exc:
            if exc.errno in {errno.EIO, errno.EBADF}:
                raise EOFError("Pty is closed") from exc
            raise
        if not data:
            raise EOFError("Pty is closed")
        return data.decode("utf-8", errors="replace")

    def write(self, value: str) -> int:
        if self.closed:
            raise EOFError("Pty is closed")
        return os.write(self.master_fd, value.encode("utf-8"))

    def isalive(self) -> bool:
        alive = self.process.poll() is None
        if not alive:
            self.exitstatus = self.process.returncode
        return alive

    def wait(self) -> int:
        self.exitstatus = self.process.wait()
        return int(self.exitstatus)

    def _signal_group(self, sig: int) -> None:
        try:
            os.killpg(os.getpgid(self.process.pid), sig)
        except ProcessLookupError:
            pass

    def terminate(self, force: bool = False) -> bool:
        if self.process.poll() is not None:
            self.exitstatus = self.process.returncode
            return True
        self._signal_group(signal.SIGTERM)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.exitstatus = self.process.returncode
                return True
            time.sleep(0.02)
        if force:
            self._signal_group(signal.SIGKILL)
            try:
                self.exitstatus = self.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                return False
            return True
        return False

    def kill(self, sig: Any = None) -> None:
        if self.process.poll() is None:
            self._signal_group(signal.SIGKILL)

    def close(self, force: bool = False) -> None:
        if not self.closed:
            if force and self.process.poll() is None:
                self.terminate(force=True)
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.closed = True
