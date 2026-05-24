"""Pure helpers for :class:`WebhookReceiver` — secret resolution, persistence.

Kept separate so the main receiver module stays under the B2 300-line cap.
None of these functions touch the network or aiohttp APIs directly.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stackowl.config.secret_resolver import SecretResolver
from stackowl.exceptions import ConfigurationError
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.config.webhook_settings import WebhookSourceConfig
    from stackowl.db.pool import DbPool


_INSERT_EVENT_SQL = (
    "INSERT INTO webhook_events_log (event_id, source, received_at, status) "
    "VALUES (?, ?, ?, ?)"
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


def validate_hmac_signature(secret: str, body: bytes, provided_sig: str) -> bool:
    """Constant-time HMAC-SHA256 comparison.

    ``provided_sig`` may include a ``sha256=`` prefix (GitHub convention) — we
    strip it before comparison.  Never logs ``secret`` or ``body``.
    """
    if not provided_sig:
        return False
    raw_sig = provided_sig
    if raw_sig.startswith("sha256="):
        raw_sig = raw_sig[len("sha256=") :]
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, raw_sig)


def make_event_id() -> str:
    """Return a new opaque event id (uuid4 hex)."""
    return uuid.uuid4().hex


def now_iso() -> str:
    """Return current UTC time as an ISO8601 string."""
    return datetime.now(UTC).isoformat()


async def persist_event_log(
    db: DbPool, event_id: str, source: str, received_at: str, status: str = "enqueued"
) -> None:
    """Best-effort append into ``webhook_events_log``; warn-and-continue on failure."""
    try:
        await db.execute(_INSERT_EVENT_SQL, (event_id, source, received_at, status))
    except Exception as exc:  # B5 — never silent
        log.webhook.warning(
            "[webhook] receiver_helpers.persist_event_log: insert failed",
            exc_info=exc,
            extra={"_fields": {"event_id": event_id, "source": source, "status": status}},
        )
