"""Single-pending second-device approval ceremony (AD-16, FR69): a new
device posts a name, gets back a short matching code, and is enrolled only
when an already-signed-in first device (a signed tap) or the kit's test
Telegram bot (an inline Approve tap) confirms that same request. Only one
request may be outstanding at a time -- a second concurrent request is
refused rather than silently displacing the first, which would let a
device the owner never saw slip in while they are mid-ceremony with another.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # same rationale as setup_code.py
CODE_LENGTH = 6
REQUEST_TTL_SECONDS = 300.0


class DeviceRequestError(Exception):
    """Raised for any refusal: a second concurrent request, approving an
    unknown/expired/already-approved request id, or a malformed device name."""


def _generate_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))


@dataclass
class DeviceRequest:
    request_id: str
    device_name: str
    code: str
    expires_at: float
    approved: bool = False
    enrollment_ticket: str | None = None


class DeviceRequestStore:
    """Holds at most one *pending* request at a time; approved requests are
    kept (until their own TTL) so the requesting device can keep polling
    the id it was handed at creation time for the approval outcome."""

    def __init__(self, ttl_seconds: float = REQUEST_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._pending: DeviceRequest | None = None
        self._approved: dict[str, DeviceRequest] = {}

    def _expire_pending_if_stale(self) -> None:
        if self._pending is not None and time.monotonic() > self._pending.expires_at:
            self._pending = None

    def create(self, device_name: str) -> DeviceRequest:
        self._expire_pending_if_stale()
        if self._pending is not None:
            raise DeviceRequestError("a device request is already pending")
        name = device_name.strip() if isinstance(device_name, str) else ""
        if not name:
            raise DeviceRequestError("device_name is required")
        request = DeviceRequest(
            request_id=secrets.token_urlsafe(12),
            device_name=name,
            code=_generate_code(),
            expires_at=time.monotonic() + self._ttl,
        )
        self._pending = request
        return request

    def get_pending(self) -> DeviceRequest | None:
        self._expire_pending_if_stale()
        return self._pending

    def get(self, request_id: str) -> DeviceRequest | None:
        self._expire_pending_if_stale()
        if self._pending is not None and self._pending.request_id == request_id:
            return self._pending
        approved = self._approved.get(request_id)
        if approved is not None and time.monotonic() > approved.expires_at:
            # Same TTL rule as the pending slot: a stale approved record is
            # dropped rather than polled forever (the docstring's own "until
            # their own TTL" claim, which nothing previously enforced).
            del self._approved[request_id]
            return None
        return approved

    def approve(self, request_id: str, code: str) -> DeviceRequest:
        """Approve the currently pending request. Requires BOTH the id (so a
        stale, already-approved, or superseded ceremony is never approved by
        accident) and the matching code (so the AC's "mismatched ... code
        rejected" is an explicit, checked refusal here, not merely "you
        cannot guess the id") -- a request that has since expired is no
        longer pending at all, and is refused the same way."""
        self._expire_pending_if_stale()
        if self._pending is None or self._pending.request_id != request_id:
            raise DeviceRequestError("no matching device request is pending")
        if not secrets.compare_digest(self._pending.code, code):
            raise DeviceRequestError("code does not match the pending request")
        request = self._pending
        request.approved = True
        self._pending = None
        self._approved[request.request_id] = request
        return request
