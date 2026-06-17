"""Pure helpers for :class:`WebhookReceiver` — secret resolution, persistence.

Kept separate so the main receiver module stays under the B2 300-line cap.
None of these functions touch the network or aiohttp APIs directly.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stackowl.config.secret_resolver import SecretResolver
from stackowl.exceptions import ConfigurationError
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.config.webhook_settings import WebhookSourceConfig
    from stackowl.db.pool import DbPool
    from stackowl.infra.clock import Clock


# Reuses the C1 CAS idiom (execute_returning_rowcount over INSERT..ON CONFLICT
# DO NOTHING): rowcount==1 → THIS request won the claim (fresh), ==0 → the
# event_id row already exists (replay). webhook_events_log.event_id is the PK
# (migration 0020) — no new table, no new migration.
_CLAIM_EVENT_SQL = (
    "INSERT INTO webhook_events_log (event_id, source, received_at, status) "
    "VALUES (?, ?, ?, ?) ON CONFLICT(event_id) DO NOTHING"
)


def resolve_source_secret(source: str, config: WebhookSourceConfig) -> str | None:
    """Resolve the per-source HMAC secret via :class:`SecretResolver`.

    Returns ``None`` (and logs) when the reference cannot be resolved — callers
    treat this as an unconfigured source rather than crashing the listener.
    """
    log.webhook.debug(
        "[webhook] receiver_helpers.resolve_source_secret: entry",
        extra={"_fields": {"source": source}},
    )
    try:
        secret = SecretResolver.resolve(config.secret)
    except ConfigurationError as exc:  # B5 — never silent
        log.webhook.warning(
            "[webhook] receiver_helpers.resolve_source_secret: resolution failed",
            exc_info=exc,
            extra={"_fields": {"source": source}},
        )
        return None
    log.webhook.debug(
        "[webhook] receiver_helpers.resolve_source_secret: exit",
        extra={"_fields": {"source": source, "secret_len": len(secret)}},
    )
    return secret


#: A SHA-256 hexdigest is EXACTLY 64 lowercase/uppercase hex chars. Any provided
#: signature must match this BEFORE the constant-time compare (F141) — a non-hex
#: or wrong-length value is malformed and rejected up front (defense-in-depth;
#: compare_digest is already length-safe, but a strict format gate documents the
#: contract and refuses garbage early without ever leaking timing/format info).
_SHA256_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def validate_hmac_signature(secret: str, body: bytes, provided_sig: str) -> bool:
    """Constant-time HMAC-SHA256 comparison with an up-front hex-format guard.

    ``provided_sig`` may include a ``sha256=`` prefix (GitHub convention) — we
    strip it before comparison. F141: the stripped value is bounded to
    ``^[0-9a-fA-F]{64}$`` BEFORE :func:`hmac.compare_digest`; a malformed
    (non-hex / wrong-length) signature is rejected as ``False``. Never logs
    ``secret`` or ``body``.
    """
    if not provided_sig:
        return False
    raw_sig = provided_sig
    if raw_sig.startswith("sha256="):
        raw_sig = raw_sig[len("sha256=") :]
    if not _SHA256_HEX_RE.match(raw_sig):
        log.webhook.warning(
            "[webhook] receiver_helpers.validate_hmac_signature: malformed signature format — rejecting",
            extra={"_fields": {"sig_len": len(raw_sig)}},
        )
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, raw_sig)


def make_event_id() -> str:
    """Return a new opaque event id (uuid4 hex). Retained as a public trace id."""
    return uuid.uuid4().hex


def derive_event_id(source: str, signed_timestamp: str, body: bytes) -> str:
    """Server-derived, attacker-immutable dedup key = sha256(source||ts||body) (R5).

    Unlike a fresh uuid4 (which makes every byte-identical replay look unique) or
    a sender delivery-id (attacker-controlled on replay), this key is derived
    from the SIGNED timestamp + body, so a captured request always derives the
    SAME id and the dedup row collides. Returns a 64-char hex digest.
    """
    h = hashlib.sha256()
    h.update(source.encode("utf-8"))
    h.update(b"\x1f")
    h.update(signed_timestamp.encode("utf-8"))
    h.update(b"\x1f")
    h.update(body)
    return h.hexdigest()


def validate_signed_timestamp(
    secret: str,
    timestamp: str,
    body: bytes,
    provided_sig: str,
    tolerance_s: int,
    clock: Clock,
) -> bool:
    """Verify a Stripe-style signed timestamp: HMAC over ``f"{ts}.".encode()+body``.

    Binds the timestamp INTO the signature so it cannot be tampered independently
    of the body (a forged ts fails the constant-time HMAC check). Also rejects a
    ts whose absolute age exceeds ``tolerance_s`` — using the injected
    :class:`Clock` (never raw time), so the window is testable and cross-platform.
    Never logs ``secret`` / ``body``.
    """
    if not provided_sig or not timestamp:
        return False
    # Freshness window first (cheap), via the injected clock.
    try:
        ts_dt = datetime.fromisoformat(timestamp)
    except (ValueError, TypeError) as exc:  # B5 — never silent
        log.webhook.warning(
            "[webhook] receiver_helpers.validate_signed_timestamp: unparseable timestamp",
            exc_info=exc,
        )
        return False
    if ts_dt.tzinfo is None:
        ts_dt = ts_dt.replace(tzinfo=UTC)
    age = abs((clock.now() - ts_dt).total_seconds())
    if age > tolerance_s:
        log.webhook.warning(
            "[webhook] receiver_helpers.validate_signed_timestamp: stale timestamp",
            extra={"_fields": {"age_s": int(age), "tolerance_s": tolerance_s}},
        )
        return False
    # Constant-time HMAC over the signed payload (ts bound into the signature).
    raw_sig = provided_sig
    if raw_sig.startswith("sha256="):
        raw_sig = raw_sig[len("sha256=") :]
    signed_payload = f"{timestamp}.".encode() + body
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, raw_sig)


async def claim_delivery(
    db: DbPool, event_id: str, source: str, received_at: str
) -> bool:
    """Atomically claim ``event_id`` for THIS request; True iff it is fresh.

    Reuses the C1 CAS primitive (``execute_returning_rowcount`` over INSERT..
    ON CONFLICT DO NOTHING). rowcount==1 → won (fresh, proceed to enqueue);
    rowcount==0 → the id already exists (replay, suppress). Fail-closed: a write
    failure PROPAGATES (R7) — a gate that swallows its own write is not a gate.
    """
    # 1. ENTRY
    log.webhook.debug(
        "[webhook] receiver_helpers.claim_delivery: entry",
        extra={"_fields": {"source": source}},
    )
    rows = await db.execute_returning_rowcount(
        _CLAIM_EVENT_SQL, (event_id, source, received_at, "enqueued")
    )
    won = rows == 1
    # 4. EXIT
    log.webhook.debug(
        "[webhook] receiver_helpers.claim_delivery: exit",
        extra={"_fields": {"source": source, "won": won}},
    )
    if not won:
        log.webhook.info(
            "[webhook] receiver_helpers.claim_delivery: replay suppressed",
            extra={"_fields": {"source": source}},
        )
    return won


def now_iso() -> str:
    """Return current UTC time as an ISO8601 string."""
    return datetime.now(UTC).isoformat()
