from __future__ import annotations

import time
import unittest

from bridge.approval_service import ApprovalService, action_fingerprint


class ApprovalServiceTests(unittest.TestCase):
    def test_pending_request_is_reused_for_identical_action(self) -> None:
        service = ApprovalService(ttl_seconds=60)
        payload = {"path": "a.txt", "content_sha256": "abc"}
        first = service.request("write_file", payload, title="write", details={"path": "a.txt"})
        second = service.request("write_file", payload, title="write", details={"path": "a.txt"})
        self.assertEqual(first["request_id"], second["request_id"])
        self.assertEqual(service.list_requests()["pending"], 1)

    def test_approved_request_is_fingerprint_bound_and_one_time(self) -> None:
        service = ApprovalService(ttl_seconds=60)
        payload = {"command": "echo one", "cwd": "."}
        request = service.request("run_command", payload, title="command")
        service.decide(request["request_id"], "approve")
        with self.assertRaisesRegex(PermissionError, "APPROVAL_FINGERPRINT_MISMATCH"):
            service.consume(request["request_id"], "run_command", {"command": "echo two", "cwd": "."})
        consumed = service.consume(request["request_id"], "run_command", payload)
        self.assertEqual(consumed["status"], "consumed")
        with self.assertRaisesRegex(PermissionError, "APPROVAL_NOT_APPROVED: consumed"):
            service.consume(request["request_id"], "run_command", payload)

    def test_denied_request_cannot_be_consumed(self) -> None:
        service = ApprovalService(ttl_seconds=60)
        payload = {"patch_sha256": "abc"}
        request = service.request("apply_patch", payload, title="patch")
        denied = service.decide(request["request_id"], "deny")
        self.assertEqual(denied["status"], "denied")
        with self.assertRaisesRegex(PermissionError, "APPROVAL_NOT_APPROVED: denied"):
            service.consume(request["request_id"], "apply_patch", payload)

    def test_expired_request_cannot_be_approved_or_consumed(self) -> None:
        service = ApprovalService(ttl_seconds=30)
        payload = {"path": "a.txt"}
        request = service.request("write_file", payload, title="write")
        service._requests[request["request_id"]]["expires_epoch"] = time.time() - 1  # controlled expiry fixture
        with self.assertRaisesRegex(RuntimeError, "APPROVAL_NOT_PENDING: expired"):
            service.decide(request["request_id"], "approve")
        with self.assertRaisesRegex(PermissionError, "APPROVAL_NOT_APPROVED: expired"):
            service.consume(request["request_id"], "write_file", payload)

    def test_request_details_are_redacted_but_fingerprint_uses_exact_payload(self) -> None:
        service = ApprovalService(ttl_seconds=60)
        payload = {"command": "echo TOKEN=private-value"}
        request = service.request(
            "run_command",
            payload,
            title="command",
            details={"command_preview": "echo TOKEN=private-value"},
        )
        rendered = str(request)
        self.assertNotIn("private-value", rendered)
        self.assertIn("[REDACTED]", rendered)
        self.assertNotEqual(
            action_fingerprint("run_command", payload),
            action_fingerprint("run_command", {"command": "echo TOKEN=other-value"}),
        )

    def test_require_or_request_returns_retry_contract(self) -> None:
        service = ApprovalService(ttl_seconds=60)
        payload = {"path": "a.txt", "content_sha256": "abc"}
        result = service.require_or_request(None, "write_file", payload, title="write")
        self.assertTrue(result["approval_required"])
        request_id = result["approval"]["request_id"]
        service.decide(request_id, "approve")
        self.assertIsNone(service.require_or_request(request_id, "write_file", payload, title="write"))


if __name__ == "__main__":
    unittest.main()
