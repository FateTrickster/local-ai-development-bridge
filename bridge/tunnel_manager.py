from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .activity_service import redact_text


_QUICK_TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)


def extract_quick_tunnel_url(text: str) -> str | None:
    for match in _QUICK_TUNNEL_RE.finditer(text):
        url = match.group(0).rstrip("/")
        hostname = urlparse(url).hostname
        if hostname and hostname.casefold() != "api.trycloudflare.com":
            return url
    return None


def tunnel_hostname(public_origin: str) -> str:
    parsed = urlparse(public_origin)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"INVALID_TUNNEL_ORIGIN: {public_origin}")
    return parsed.hostname


def discover_cloudflared(explicit: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env_path = os.environ.get("CLOUDFLARED_PATH", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())
    found = shutil.which("cloudflared")
    if found:
        candidates.append(Path(found))
    if os.name == "nt":
        for key in ("ProgramFiles(x86)", "ProgramFiles"):
            base = os.environ.get(key)
            if base:
                candidates.append(Path(base) / "cloudflared" / "cloudflared.exe")
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        marker = str(resolved).casefold()
        if marker in seen:
            continue
        seen.add(marker)
        if resolved.is_file():
            return resolved
    raise FileNotFoundError(
        "CLOUDFLARED_NOT_FOUND: install cloudflared, put it on PATH, or set CLOUDFLARED_PATH"
    )


class CloudflareQuickTunnel:
    """Manage one cloudflared Quick Tunnel process and expose sanitized status."""

    def __init__(
        self,
        origin: str,
        *,
        executable: str | Path | None = None,
        startup_timeout: float = 30.0,
        on_line: Callable[[str], None] | None = None,
        popen_factory: Callable[..., Any] = subprocess.Popen,
    ):
        self.origin = origin.rstrip("/")
        self.executable = discover_cloudflared(str(executable) if executable else None)
        self.startup_timeout = max(3.0, float(startup_timeout))
        self.on_line = on_line
        self._popen_factory = popen_factory
        self.process: Any | None = None
        self.public_origin: str | None = None
        self.hostname: str | None = None
        self._started_at: float | None = None
        self._reader_thread: threading.Thread | None = None
        self._url_ready = threading.Event()
        self._lines: deque[str] = deque(maxlen=120)
        self._line_queue: queue.Queue[str] = queue.Queue(maxsize=256)
        self._lock = threading.RLock()

    def _handle_line(self, raw: str) -> None:
        line = redact_text(raw.rstrip("\r\n"), 4000)
        if not line:
            return
        with self._lock:
            self._lines.append(line)
            url = extract_quick_tunnel_url(line)
            if url and self.public_origin is None:
                self.public_origin = url
                self.hostname = tunnel_hostname(url)
                self._url_ready.set()
        if self.on_line is not None:
            self.on_line(line)
        try:
            self._line_queue.put_nowait(line)
        except queue.Full:
            try:
                self._line_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._line_queue.put_nowait(line)
            except queue.Full:
                pass

    def _reader(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                self._handle_line(line)
        finally:
            self._url_ready.set()

    def start(self, env: dict[str, str] | None = None) -> dict[str, Any]:
        with self._lock:
            if self.process is not None and self.process.poll() is None:
                return self.status()
            command = [str(self.executable), "tunnel", "--url", self.origin, "--no-autoupdate"]
            self.process = self._popen_factory(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env or os.environ.copy(),
                bufsize=1,
            )
            self._started_at = time.monotonic()
            self.public_origin = None
            self.hostname = None
            self._url_ready.clear()
            self._reader_thread = threading.Thread(target=self._reader, daemon=True, name="cloudflared-reader")
            self._reader_thread.start()

        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.public_origin:
                return self.status()
            process = self.process
            if process is not None and process.poll() is not None:
                tail = "\n".join(self.recent_lines(20))
                raise RuntimeError(f"CLOUDFLARED_EXITED_{process.returncode}: {tail}")
            self._url_ready.wait(timeout=0.2)
        self.stop()
        tail = "\n".join(self.recent_lines(20))
        raise TimeoutError(f"QUICK_TUNNEL_URL_TIMEOUT after {self.startup_timeout:.1f}s: {tail}")

    def recent_lines(self, limit: int = 20) -> list[str]:
        with self._lock:
            return list(self._lines)[-max(1, min(int(limit), 120)) :]

    def status(self) -> dict[str, Any]:
        with self._lock:
            process = self.process
            running = process is not None and process.poll() is None
            elapsed_ms = None
            if self._started_at is not None:
                elapsed_ms = round((time.monotonic() - self._started_at) * 1000)
            return {
                "provider": "cloudflare-quick",
                "running": running,
                "pid": getattr(process, "pid", None) if process is not None else None,
                "origin": self.origin,
                "public_origin": self.public_origin,
                "hostname": self.hostname,
                "exit_code": None if running or process is None else process.poll(),
                "elapsed_ms": elapsed_ms,
                "recent_log": self.recent_lines(8),
            }

    def stop(self, timeout: float = 5.0) -> dict[str, Any]:
        process = self.process
        if process is None:
            return self.status()
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=max(0.5, timeout))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.5)
        return self.status()
