from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bridge.config import Settings
from bridge.pathguard import WorkspaceGuard
from bridge.vscode_service import VSCodeService


class _Handler(BaseHTTPRequestHandler):
    token = "test-token"
    workspace_root = ""
    requests: list[dict] = []

    def log_message(self, format, *args):
        pass

    def _authorized(self) -> bool:
        return self.headers.get("x-local-ai-bridge-token") == self.token

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._authorized():
            self._send(404, {"error": "not_found"})
            return
        if self.path == "/health":
            self._send(200, {"ready": True, "workspace_folders": [self.workspace_root], "open_documents": []})
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self):
        if not self._authorized():
            self._send(404, {"error": "not_found"})
            return
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.requests.append({"path": self.path, "body": body})
        if self.path == "/diagnostics":
            self._send(200, {"provider_state": "ready", "results": [], "truncated": False})
        elif self.path == "/lsp":
            self._send(200, {"provider_state": "ready", "operation": body.get("operation"), "results": [], "truncated": False})
        elif self.path == "/document":
            self._send(200, {"provider_state": "ready", "content": "dirty buffer", "document_dirty": True})
        else:
            self._send(404, {"error": "not_found"})


class VSCodeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "workspace"
        self.root.mkdir()
        (self.root / "sample.py").write_text("x = 1\n", encoding="utf-8")
        self.state = Path(self.temp.name) / "state"
        self.state.mkdir()
        self.settings = Settings(
            workspace_root=self.root,
            allow_write=False,
            allow_commands=False,
            max_read_bytes=262_144,
            max_search_results=500,
            max_search_bytes=262_144,
            max_directory_entries=500,
        )
        self.guard = WorkspaceGuard(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_missing_companion_returns_not_ready(self) -> None:
        service = VSCodeService(self.settings, self.guard, data_dir=self.state, timeout_seconds=0.5)
        result = service.health()
        self.assertEqual(result["provider_state"], "not_ready")
        self.assertIn("VSCODE_COMPANION_NOT_RUNNING", result["provider_state_reason"])

    def test_companion_requests_are_authenticated_and_paths_are_absolute(self) -> None:
        _Handler.workspace_root = str(self.root)
        _Handler.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            (self.state / "vscode-token.txt").write_text(_Handler.token, encoding="utf-8")
            (self.state / "vscode-companion.json").write_text(json.dumps({"port": port, "pid": 1}), encoding="utf-8")
            service = VSCodeService(self.settings, self.guard, data_dir=self.state, timeout_seconds=1.0)

            health = service.health()
            self.assertEqual(health["provider_state"], "ready")
            diagnostics = service.get_diagnostics("sample.py", ["error"], 25)
            self.assertEqual(diagnostics["provider_state"], "ready")
            lsp = service.lsp("definition", "sample.py", 1, 1)
            self.assertEqual(lsp["operation"], "definition")
            self.assertEqual(lsp["semantic_state"], "READY_EMPTY")
            self.assertTrue(lsp["semantic_result_inconclusive"])
            self.assertTrue(lsp["legacy_semantics"])
            buffer = service.read_editor_buffer("sample.py")
            self.assertTrue(buffer["document_dirty"])

            sent_paths = [_request["path"] for _request in _Handler.requests]
            self.assertEqual(sent_paths, ["/diagnostics", "/lsp", "/document"])
            for request in _Handler.requests:
                if "path" in request["body"]:
                    self.assertEqual(Path(request["body"]["path"]).resolve(), (self.root / "sample.py").resolve())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_v2_semantic_payload_is_preserved(self) -> None:
        result = VSCodeService._normalize_lsp_result(
            "hover",
            {
                "provider_state": "ready",
                "provider_state_reason": "Provider query completed with results",
                "semantic_state": "READY_WITH_RESULTS",
                "semantic_result_inconclusive": False,
                "operation": "hover",
                "document_state": "DOCUMENT_OPEN",
                "results": [{"contents": ["value"]}],
                "truncated": False,
            },
        )
        self.assertEqual(result["semantic_state"], "READY_WITH_RESULTS")
        self.assertFalse(result["semantic_result_inconclusive"])
        self.assertEqual(result["semantic_contract_version"], 2)
        self.assertNotIn("legacy_semantics", result)

    def test_semantic_reason_mapping(self) -> None:
        self.assertEqual(VSCodeService._semantic_state_from_reason("LSP_PROVIDER_TIMEOUT: hover"), "TIMEOUT")
        self.assertEqual(VSCodeService._semantic_state_from_reason("PATH_OUTSIDE_VSCODE_WORKSPACE"), "WORKSPACE_MISMATCH")
        self.assertEqual(VSCodeService._semantic_state_from_reason("PROVIDER_NOT_AVAILABLE"), "PROVIDER_NOT_AVAILABLE")
        self.assertEqual(VSCodeService._semantic_state_from_reason("PATH_REQUIRED"), "INVALID_REQUEST")
        self.assertEqual(VSCodeService._semantic_state_from_reason("other failure"), "PROVIDER_ERROR")

    def test_workspace_mismatch_is_reported(self) -> None:
        _Handler.workspace_root = str(Path(self.temp.name) / "other")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            (self.state / "vscode-token.txt").write_text(_Handler.token, encoding="utf-8")
            (self.state / "vscode-companion.json").write_text(json.dumps({"port": port}), encoding="utf-8")
            service = VSCodeService(self.settings, self.guard, data_dir=self.state, timeout_seconds=1.0)
            result = service.health()
            self.assertEqual(result["provider_state"], "not_ready")
            self.assertEqual(result["provider_state_reason"], "VSCODE_WORKSPACE_MISMATCH")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
