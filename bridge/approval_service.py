from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .activity_service import redact_value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def action_fingerprint(action: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"action": str(action), "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


class ApprovalService:
    """Ephemeral, local-user approval state for sensitive MCP actions.

    Request IDs are intentionally not secrets: the MCP caller receives them so it
    can retry the same action after a local user decision. The authority to change
    request status lives only in the localhost Dashboard, protected by a separate
    ephemeral dashboard token. An approved request is fingerprint-bound and can be
    consumed exactly once.
    """

    def __init__(self, *, ttl_seconds: int = 300, max_requests: int = 200):
        self.ttl_seconds = max(30, min(int(ttl_seconds), 3600))
        self.max_requests = max(20, min(int(max_requests), 1000))
        self._lock = threading.RLock()
        self._requests: dict[str, dict[str, Any]] = {}
        self._dashboard_token = secrets.token_urlsafe(32)

    @property
    def dashboard_token(self) -> str:
        return self._dashboard_token

    def _expire_locked(self) -> None:
        now = time.time()
        for item in self._requests.values():
            if item.get("status") in {"pending", "approved"} and float(item.get("expires_epoch", 0)) <= now:
                item["status"] = "expired"
                item["decided_at"] = _now_iso()
        if len(self._requests) <= self.max_requests:
            return
        ordered = sorted(
            self._requests.values(),
            key=lambda item: float(item.get("created_epoch", 0)),
        )
        removable = [item for item in ordered if item.get("status") in {"denied", "expired", "consumed"}]
        for item in removable[: max(0, len(self._requests) - self.max_requests)]:
            self._requests.pop(str(item["request_id"]), None)

    def request(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        title: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fingerprint = action_fingerprint(action, payload)
        now = time.time()
        with self._lock:
            self._expire_locked()
            # Reuse an already-pending identical request rather than flooding the UI
            # when an MCP client retries before the human has decided.
            for item in self._requests.values():
                if (
                    item.get("status") == "pending"
                    and item.get("action") == action
                    and item.get("fingerprint") == fingerprint
                    and float(item.get("expires_epoch", 0)) > now
                ):
                    return self._public(item)
            request_id = str(uuid.uuid4())
            created_at = _now_iso()
            item: dict[str, Any] = {
                "request_id": request_id,
                "action": str(action),
                "fingerprint": fingerprint,
                "title": str(redact_value(str(title)))[:400],
                "details": redact_value(details or {}),
                "status": "pending",
                "created_at": created_at,
                "created_epoch": now,
                "expires_at_epoch": now + self.ttl_seconds,
                "expires_epoch": now + self.ttl_seconds,
                "ttl_seconds": self.ttl_seconds,
                "decided_at": None,
                "consumed_at": None,
            }
            self._requests[request_id] = item
            self._expire_locked()
            return self._public(item)

    @staticmethod
    def _public(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "request_id": item.get("request_id"),
            "action": item.get("action"),
            "title": item.get("title"),
            "details": item.get("details", {}),
            "status": item.get("status"),
            "created_at": item.get("created_at"),
            "expires_epoch": item.get("expires_epoch"),
            "ttl_seconds": item.get("ttl_seconds"),
            "decided_at": item.get("decided_at"),
            "consumed_at": item.get("consumed_at"),
        }

    def list_requests(self, *, include_terminal: bool = True, limit: int = 100) -> dict[str, Any]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            self._expire_locked()
            values = list(self._requests.values())
            if not include_terminal:
                values = [item for item in values if item.get("status") in {"pending", "approved"}]
            values.sort(key=lambda item: float(item.get("created_epoch", 0)), reverse=True)
            public = [self._public(item) for item in values[:limit]]
            pending = sum(1 for item in self._requests.values() if item.get("status") == "pending")
            approved = sum(1 for item in self._requests.values() if item.get("status") == "approved")
            return {
                "requests": public,
                "pending": pending,
                "approved": approved,
                "ttl_seconds": self.ttl_seconds,
            }

    def decide(self, request_id: str, decision: str) -> dict[str, Any]:
        normalized = str(decision).strip().lower()
        if normalized not in {"approve", "deny"}:
            raise ValueError("INVALID_APPROVAL_DECISION")
        with self._lock:
            self._expire_locked()
            item = self._requests.get(request_id)
            if item is None:
                raise KeyError("APPROVAL_REQUEST_NOT_FOUND")
            if item.get("status") != "pending":
                raise RuntimeError(f"APPROVAL_NOT_PENDING: {item.get('status')}")
            item["status"] = "approved" if normalized == "approve" else "denied"
            item["decided_at"] = _now_iso()
            return self._public(item)

    def consume(self, request_id: str | None, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not request_id:
            raise PermissionError("APPROVAL_REQUIRED")
        fingerprint = action_fingerprint(action, payload)
        with self._lock:
            self._expire_locked()
            item = self._requests.get(str(request_id))
            if item is None:
                raise PermissionError("APPROVAL_REQUEST_NOT_FOUND")
            status = str(item.get("status"))
            if status != "approved":
                raise PermissionError(f"APPROVAL_NOT_APPROVED: {status}")
            if item.get("action") != action or not secrets.compare_digest(str(item.get("fingerprint")), fingerprint):
                raise PermissionError("APPROVAL_FINGERPRINT_MISMATCH")
            item["status"] = "consumed"
            item["consumed_at"] = _now_iso()
            return self._public(item)

    def require_or_request(
        self,
        request_id: str | None,
        action: str,
        payload: dict[str, Any],
        *,
        title: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if request_id:
            self.consume(request_id, action, payload)
            return None
        request = self.request(action, payload, title=title, details=details)
        return {
            "approval_required": True,
            "approval": request,
            "message": "Local approval is required. Approve this request in the localhost Dashboard, then retry the exact same action with approval_id=request_id.",
        }
