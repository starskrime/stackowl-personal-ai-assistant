"""DeliveryLedger — occurrence-scoped exactly-once delivery (C1 / F103 delivery half).

The poller CAS (``execute_returning_rowcount`` guarded ``pending->running``)
stops two dispatchers double-firing, but a crash BETWEEN a successful send and the
``job_runs`` completion INSERT is replayed by ``recover()`` and re-runs the handler
-> a second user-visible brief. This ledger closes that gap.

A proactive surface (handler / event bridge) calls :meth:`claim_dispatch` BEFORE the
side-effect: it atomically pre-records a ``dispatched`` row keyed by
``(job_id, occurrence_key, channel)`` via an ``INSERT ... ON CONFLICT DO NOTHING``
and returns whether IT won the claim (rowcount == 1). On replay the row already
exists, the claim loses (rowcount == 0) and the re-send is suppressed. After
transport returns, :meth:`mark` flips the row to ``delivered`` / ``failed``.

The key is OCCURRENCE-scoped (``occurrence_key = idempotency_key@next_run_at``),
NOT job-scoped, so the frozen-scheduler fix (migration 0040) is preserved: a later
scheduled instant is a fresh occurrence_key and a legitimately new delivery.

Reuses :meth:`DbPool.execute_returning_rowcount` — the SAME single-serialized-
connection compare-and-swap primitive the scheduler CAS and B4 crash-recovery use.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only import
    from stackowl.db.pool import DbPool

LedgerState = Literal["dispatched", "delivered", "failed"]

# A FAILED prior attempt for the same occurrence+channel is RE-CLAIMABLE (migration
# 0055's documented intent: "A 'failed' row permits an honest retry next run"). The
# conflict target re-arms a 'failed' row back to 'dispatched' (rowcount 1 = won), but
# leaves a 'delivered' (exactly-once lock) or in-flight 'dispatched' row untouched
# (WHERE false -> rowcount 0 = lost -> the caller suppresses the re-send). Without
# this, a single transient send failure permanently burned the occurrence.
_CLAIM_SQL = (
    "INSERT INTO delivery_attempts "
    "(job_id, occurrence_key, channel, state, created_at, updated_at) "
    "VALUES (?, ?, ?, 'dispatched', ?, ?) "
    "ON CONFLICT(job_id, occurrence_key, channel) DO UPDATE "
    "SET state = 'dispatched', updated_at = excluded.updated_at "
    "WHERE delivery_attempts.state = 'failed'"
)
_MARK_SQL = (
    "UPDATE delivery_attempts SET state = ?, updated_at = ? "
    "WHERE job_id = ? AND occurrence_key = ? AND channel = ?"
)


class DeliveryLedger:
    """Atomic pre-record + state transition for one delivery attempt."""

    def __init__(self, db: DbPool) -> None:
        self._db = db

    async def claim_dispatch(
        self, job_id: str, occurrence_key: str, channel: str
    ) -> bool:
        """Atomically pre-record a ``dispatched`` row; True iff THIS caller won.

        A return of ``False`` means a ``delivered`` (exactly-once lock) or in-flight
        ``dispatched`` row already exists for this exact occurrence+channel, so the
        caller MUST suppress the re-send. A prior ``failed`` attempt is instead
        RE-CLAIMED (re-armed to ``dispatched``, returns ``True``) so a transient send
        failure can honestly retry on a later run instead of permanently burning the
        occurrence. Reuses the single-serialized-connection CAS primitive
        (``execute_returning_rowcount``) — rowcount 1 = inserted-or-reclaimed (won),
        0 = the ``ON CONFLICT`` no-op (already delivered / in-flight, lost).
        """
        # 1. ENTRY
        log.notifications.debug(
            "[notifications] delivery_ledger.claim_dispatch: entry",
            extra={"_fields": {"job_id": job_id, "channel": channel}},
        )
        now = datetime.now(UTC).isoformat()
        try:
            rows = await self._db.execute_returning_rowcount(
                _CLAIM_SQL, (job_id, occurrence_key, channel, now, now)
            )
        except Exception as exc:  # B5 — never silent
            log.notifications.error(
                "[notifications] delivery_ledger.claim_dispatch: insert failed",
                exc_info=exc,
                extra={"_fields": {"job_id": job_id, "channel": channel}},
            )
            raise
        won = rows == 1
        # 2. DECISION + 4. EXIT
        log.notifications.debug(
            "[notifications] delivery_ledger.claim_dispatch: exit",
            extra={"_fields": {"job_id": job_id, "channel": channel, "won": won}},
        )
        if not won:
            log.notifications.info(
                "[notifications] delivery_ledger.claim_dispatch: already dispatched "
                "— suppressing replay re-send",
                extra={
                    "_fields": {
                        "job_id": job_id,
                        "occurrence_key": occurrence_key,
                        "channel": channel,
                    }
                },
            )
        return won

    async def mark(
        self, job_id: str, occurrence_key: str, channel: str, state: LedgerState
    ) -> None:
        """Flip a claimed row to ``delivered`` / ``failed`` after transport returns."""
        # 1. ENTRY
        log.notifications.debug(
            "[notifications] delivery_ledger.mark: entry",
            extra={"_fields": {"job_id": job_id, "channel": channel, "state": state}},
        )
        now = datetime.now(UTC).isoformat()
        try:
            await self._db.execute(
                _MARK_SQL, (state, now, job_id, occurrence_key, channel)
            )
        except Exception as exc:  # B5 — never silent
            log.notifications.error(
                "[notifications] delivery_ledger.mark: update failed",
                exc_info=exc,
                extra={"_fields": {"job_id": job_id, "channel": channel, "state": state}},
            )
            raise
        # 4. EXIT
        log.notifications.debug(
            "[notifications] delivery_ledger.mark: exit",
            extra={"_fields": {"job_id": job_id, "channel": channel, "state": state}},
        )
