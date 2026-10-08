from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable


_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_RUNTIME_DIR = _PACKAGE_ROOT / ".runtime"
_AUDIT_DIR = _PACKAGE_ROOT / ".audit"
_TOKEN_FILE = _RUNTIME_DIR / "access-token.txt"
_AUDIT_FILE = _AUDIT_DIR / "requests.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get_or_create_token() -> str:
    env_token = os.environ.get("BRIDGE_TOKEN", "").strip()
    if env_token:
        return env_token
    _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if _TOKEN_FILE.is_file():
        existing = _TOKEN_FILE.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    token = secrets.token_urlsafe(32)
    temporary = _TOKEN_FILE.with_suffix(".tmp")
    temporary.write_text(token, encoding="utf-8")
    temporary.replace(_TOKEN_FILE)
    return token


class AuditLogger:
    def __init__(self, path: Path = _AUDIT_FILE):
        self.path = path
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **details: Any) -> None:
        record = {"timestamp": _now_iso(), "event": event, **details}
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)


class CapabilityAuthApp:
    """ASGI wrapper that protects the MCP app with a persistent capability token.

    Clients that cannot set custom Authorization headers can place the token in the
    URL path: /<token>/mcp. Clients that can set a Bearer token may use /mcp with
    Authorization: Bearer <token>.
    """

    def __init__(self, app: Callable[..., Awaitable[Any]], token: str, audit: AuditLogger | None = None):
        self.app = app
        self.token = token
        self.prefix = f"/{token}"
        self.audit = audit or AuditLogger()

    @staticmethod
    async def _send_json(send, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    def _bearer_ok(self, scope: dict[str, Any]) -> bool:
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw = headers.get(b"authorization", b"").decode("latin1")
        if not raw.lower().startswith("bearer "):
            return False
        candidate = raw[7:].strip()
        return secrets.compare_digest(candidate, self.token)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope.get("type") not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        authorized = False
        rewritten = path
        auth_mode = "none"
        if path == self.prefix or path.startswith(self.prefix + "/"):
            rewritten = path[len(self.prefix) :] or "/"
            authorized = True
            auth_mode = "capability_path"
        elif self._bearer_ok(scope):
            authorized = True
            auth_mode = "bearer"

        client = scope.get("client")
        client_host = client[0] if isinstance(client, (list, tuple)) and client else None
        if not authorized:
            self.audit.write("request_denied", method=scope.get("method"), path=path, client=client_host)
            if scope.get("type") == "http":
                await self._send_json(send, 404, {"error": "not_found"})
            else:
                await send({"type": "websocket.close", "code": 4404})
            return

        new_scope = dict(scope)
        new_scope["path"] = rewritten
        raw_path = scope.get("raw_path")
        if isinstance(raw_path, (bytes, bytearray)):
            new_scope["raw_path"] = rewritten.encode("utf-8")
        self.audit.write(
            "request_allowed",
            method=scope.get("method"),
            path=rewritten,
            auth_mode=auth_mode,
            client=client_host,
        )
        await self.app(new_scope, receive, send)


def build_secure_mcp_app(mcp) -> tuple[CapabilityAuthApp, str]:
    token = get_or_create_token()
    return CapabilityAuthApp(mcp.streamable_http_app(), token), token
