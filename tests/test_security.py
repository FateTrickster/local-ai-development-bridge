from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from bridge.security import AuditLogger, CapabilityAuthApp


async def invoke(app, path: str, headers: list[tuple[bytes, bytes]] | None = None):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    }
    await app(scope, receive, send)
    return sent


class SecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.audit_path = Path(self.temp.name) / "audit.jsonl"
        self.seen_paths: list[str] = []

        async def inner(scope, receive, send):
            self.seen_paths.append(scope["path"])
            body = b"ok"
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"2")]})
            await send({"type": "http.response.body", "body": body})

        self.app = CapabilityAuthApp(inner, "secret-token", AuditLogger(self.audit_path))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_unknown_path_is_hidden_with_404(self) -> None:
        sent = asyncio.run(invoke(self.app, "/mcp"))
        start = next(message for message in sent if message["type"] == "http.response.start")
        self.assertEqual(start["status"], 404)
        self.assertEqual(self.seen_paths, [])

    def test_capability_path_is_rewritten(self) -> None:
        sent = asyncio.run(invoke(self.app, "/secret-token/mcp"))
        start = next(message for message in sent if message["type"] == "http.response.start")
        self.assertEqual(start["status"], 200)
        self.assertEqual(self.seen_paths, ["/mcp"])

    def test_bearer_token_is_supported(self) -> None:
        sent = asyncio.run(invoke(self.app, "/mcp", [(b"authorization", b"Bearer secret-token")]))
        start = next(message for message in sent if message["type"] == "http.response.start")
        self.assertEqual(start["status"], 200)
        self.assertEqual(self.seen_paths, ["/mcp"])

    def test_audit_log_does_not_store_capability_token_for_allowed_request(self) -> None:
        asyncio.run(invoke(self.app, "/secret-token/mcp"))
        record = json.loads(self.audit_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(record["event"], "request_allowed")
        self.assertEqual(record["path"], "/mcp")
        self.assertNotIn("secret-token", json.dumps(record))


if __name__ == "__main__":
    unittest.main()
