"""UndeliveredOutbox — PA5(b) silent-delivery gate (the durable NACK).

Background. Proactive/scheduled output that cannot be delivered NOW is, on
several paths, DROPPED today (only a hash audit row remains, the body is gone):
* deliverer.py terminal ``failed`` (transport retry + fallback-reroute exhausted)
* router.py ``suppressed`` branch (low urgency under hard focus)
* proactive_job.py ``no_deliverer`` rollup (a channel had no durable recipient)

That violates the arc invariant: uncertainty fails CLOSED with a durable NACK,
never a silent log. This store closes the hole — every silent-drop seam writes a
``undelivered_outbox`` row carrying the body + reason; the assemble step surfaces
pending rows as a banner on the user's next real inbound turn, then
``mark_surfaced`` clears them so the banner shows exactly once.

Distinct from sibling stores:
* ``notification_log`` is hash-only AUDIT (never the body, no surfacing).
* ``notification_queue`` carries the body but with a time-scheduled push lifecycle
  (the digest job flushes at the end of the quiet window) — NOT next-contact.
* ``delivery_attempts`` is per-occurrence exactly-once dispatch state — NOT body.

Mirrors the existing store shape (``DeliveryLedger``): a thin async class over the
single ``DbPool``, with 4-point logging on every public op. Writes are
best-effort (B5: a NACK-write failure logs but never breaks the caller — losing
the row is preferable to crashing the proactive path that was already failing).
"""

from __future__ import annotations

import time as _time
from typing import TYPE_CHECKING, Any

from stackowl.infra.observability import log
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from collections.abc import Sequence

    from stackowl.db.pool import DbPool


_INSERT_SQL = (
    "INSERT INTO undelivered_outbox "
    "(owner_id, identity_key, channel, category, urgency, body, reason, "
    " job_id, created_at, surfaced_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)"
)

_LIST_PENDING_SQL = (
    "SELECT id, channel, category, urgency, body, reason, job_id, created_at "
    "FROM undelivered_outbox "
    "WHERE owner_id = ? AND identity_key = ? AND surfaced_at IS NULL "
    "ORDER BY created_at ASC "
    "LIMIT ?"
)

_COUNT_PENDING_SQL = (
    "SELECT COUNT(*) AS n FROM undelivered_outbox "
    "WHERE owner_id = ? AND identity_key = ? AND surfaced_at IS NULL"
)


# Bounded list size for the next-contact banner. A flood is still a flood —
# the banner shows the oldest N, the rest stay pending until the next turn.
DEFAULT_LIST_LIMIT = 20

# Allowed reason set — kept here so callers and the ratchet agree on the
# vocabulary. A new reason MUST land here first; downstream tests gate on it.
ALLOWED_REASONS: frozenset[str] = frozenset(
    {"transport_failed", "suppressed", "no_deliverer", "undeliverable"}
)


class UndeliveredOutbox:
    """Thin durable store for silent-drop notifications.

    All public methods are non-raising (B5): a DB failure on a NACK-write logs
    at ``error`` and returns a typed False-ish outcome rather than propagating
    — the proactive path that produced the silent drop already failed once, and
    a second crash there would surface as a process bug rather than a typed NACK.
    """

    def __init__(self, db: DbPool) -> None:
        self._db = db

    async def record_undelivered(
        self,
        *,
        identity_key: str,
        body: str,
        reason: str,
        channel: str | None = None,
        category: str | None = None,
        urgency: str | None = None,
        job_id: str | None = None,
        owner_id: str = DEFAULT_PRINCIPAL_ID,
    ) -> bool:
        """Persist a single undelivered notification (the NACK write).

        Returns True on a successful insert, False when the write failed (logged
        at ``error``) or when the inputs were obviously unusable. Never raises —
        a NACK-write failure must not crash the caller's already-failing path.
        """
        log.notifications.debug(
            "[notifications] undelivered_outbox.record: entry",
            extra={
                "_fields": {
                    "identity_key": identity_key,
                    "reason": reason,
                    "channel": channel,
                    "body_len": len(body) if body else 0,
                }
            },
        )
        if not identity_key:
            log.notifications.warning(
                "[notifications] undelivered_outbox.record: empty identity_key — skipped",
                extra={"_fields": {"reason": reason, "channel": channel}},
            )
            return False
        if not body:
            log.notifications.warning(
                "[notifications] undelivered_outbox.record: empty body — skipped",
                extra={"_fields": {"reason": reason, "identity_key": identity_key}},
            )
            return False
        if reason not in ALLOWED_REASONS:
            log.notifications.warning(
                "[notifications] undelivered_outbox.record: unknown reason — skipped",
                extra={"_fields": {"reason": reason, "identity_key": identity_key}},
            )
            return False
        now = _time.time()
        try:
            await self._db.execute(
                _INSERT_SQL,
                (
                    owner_id,
                    identity_key,
                    channel,
                    category,
                    urgency,
                    body,
                    reason,
                    job_id,
                    now,
                ),
            )
        except Exception as exc:  # B5 — never silent, never raise
            log.notifications.error(
                "[notifications] undelivered_outbox.record: insert failed",
                exc_info=exc,
                extra={
                    "_fields": {
                        "identity_key": identity_key,
                        "reason": reason,
                        "channel": channel,
                    }
                },
            )
            return False
        log.notifications.info(
            "[notifications] undelivered_outbox.record: durable NACK written",
            extra={
                "_fields": {
                    "identity_key": identity_key,
                    "reason": reason,
                    "channel": channel,
                    "job_id": job_id,
                }
            },
        )
        return True

    async def list_pending(
        self,
        identity_key: str,
        *,
        owner_id: str = DEFAULT_PRINCIPAL_ID,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> list[dict[str, Any]]:
        """Return oldest-first pending rows for ``identity_key`` (surfaced_at IS NULL).

        Bounded by ``limit`` — a flood is still a flood; the banner shows the
        oldest N and the rest stay pending for the next turn. Empty list on any
        DB failure (the banner is best-effort).
        """
        log.notifications.debug(
            "[notifications] undelivered_outbox.list_pending: entry",
            extra={"_fields": {"identity_key": identity_key, "limit": limit}},
        )
        if not identity_key:
            return []
        try:
            rows = await self._db.fetch_all(
                _LIST_PENDING_SQL, (owner_id, identity_key, limit)
            )
        except Exception as exc:  # B5 — never silent
            log.notifications.error(
                "[notifications] undelivered_outbox.list_pending: query failed",
                exc_info=exc,
                extra={"_fields": {"identity_key": identity_key}},
            )
            return []
        out = [dict(r) for r in rows]
        log.notifications.debug(
            "[notifications] undelivered_outbox.list_pending: exit",
            extra={"_fields": {"identity_key": identity_key, "n": len(out)}},
        )
        return out

    async def mark_surfaced(
        self,
        ids: Sequence[int],
        *,
        owner_id: str = DEFAULT_PRINCIPAL_ID,
    ) -> None:
        """Set ``surfaced_at = now`` on the listed rows (the clear).

        Idempotent: re-surfacing an already-surfaced row is a no-op (the partial
        index keeps it out of the next ``list_pending``). Never raises.
        """
        if not ids:
            return
        log.notifications.debug(
            "[notifications] undelivered_outbox.mark_surfaced: entry",
            extra={"_fields": {"n": len(ids)}},
        )
        placeholders = ",".join("?" for _ in ids)
        sql = (
            "UPDATE undelivered_outbox SET surfaced_at = ? "
            f"WHERE owner_id = ? AND id IN ({placeholders}) "
            "AND surfaced_at IS NULL"
        )
        params: tuple[Any, ...] = (_time.time(), owner_id, *ids)
        try:
            await self._db.execute(sql, params)
        except Exception as exc:  # B5 — never silent
            log.notifications.error(
                "[notifications] undelivered_outbox.mark_surfaced: update failed",
                exc_info=exc,
                extra={"_fields": {"n": len(ids)}},
            )
            return
        log.notifications.debug(
            "[notifications] undelivered_outbox.mark_surfaced: exit",
            extra={"_fields": {"n": len(ids)}},
        )

    async def pending_count(
        self,
        identity_key: str,
        *,
        owner_id: str = DEFAULT_PRINCIPAL_ID,
    ) -> int:
        """Count pending rows for ``identity_key`` (cheap, for telemetry / health)."""
        if not identity_key:
            return 0
        try:
            rows = await self._db.fetch_all(
                _COUNT_PENDING_SQL, (owner_id, identity_key)
            )
        except Exception as exc:  # B5 — never silent
            log.notifications.error(
                "[notifications] undelivered_outbox.pending_count: query failed",
                exc_info=exc,
                extra={"_fields": {"identity_key": identity_key}},
            )
            return 0
        return int(rows[0]["n"]) if rows else 0


def render_banner(rows: list[dict[str, Any]]) -> str:
    """Build the user-facing next-contact banner text from pending rows.

    Pure (no I/O). Empty input ⇒ empty string (the assemble step skips
    inclusion). The body is shown verbatim so the user reads what was actually
    going to be sent — the assemble step decides where this lands in the system
    prompt.
    """
    if not rows:
        return ""
    n = len(rows)
    header = (
        f"While you were away, {n} message{'s' if n != 1 else ''} could not be "
        "delivered. Here is the content (shown once):"
    )
    lines: list[str] = []
    for r in rows:
        cat = r.get("category") or ""
        reason = r.get("reason") or ""
        prefix = f"[{cat}/{reason}]" if cat else f"[{reason}]"
        body = (r.get("body") or "").strip()
        lines.append(f"- {prefix} {body}")
    return header + "\n" + "\n".join(lines)
