"""MessageLedgerStore — universal per-message status lifecycle.

Every inbound message gets a row here at intake (``pending``), inserted
synchronously before the message touches any in-memory-only structure
(gateway turn queues/mailboxes) — closing the gap where a message that
arrived but hadn't yet reached a durable seam was silently lost on a core
crash. The row flips to ``completed`` once the turn's reply is delivered,
``failed`` if the turn floored (or was dropped on intake overflow), or
``absorbed`` if the message was folded (STEER) into another already-running
turn and never produced a reply of its own.

Owner-scoped (subclasses :class:`~stackowl.tenancy.OwnedRepository`): every
query binds ``owner_id = ?`` so a row can never be read or written across
principals. Wraps migration 0089's ``message_ledger`` table — see
``src/stackowl/db/migrations/0089_message_ledger.sql`` for the shipped
schema.

Deliberately a sibling table to ``retry_queue`` (migration 0082), not a
repurposing of it: ``retry_queue`` only ever gets a row for a floored turn
and its bookkeeping (attempt_count, banned_capabilities, next_retry_at) is
an unrelated concern from "did this message get a reply." Two single-purpose
stores, same convention as ``TaskOutcomeStore``/``DeliveryLedger``/
``RetryQueueStore`` already being separate tables for separate concerns.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID, OwnedRepository

#: Unbounded exception/floor text must not bloat the row (same rule as
#: retry_queue.last_error).
_FAILURE_REASON_MAX_LEN = 2000
#: input_text is free-text from the user and must not bloat the row (same
#: rule as retry_queue.goal).
_INPUT_TEXT_MAX_LEN = 4000


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class MessageLedgerRow:
    """Read-side projection of one message_ledger row."""

    trace_id: str
    session_id: str
    channel: str
    input_text: str
    chat_id: str | None = None
    status: str = "pending"
    failure_reason: str | None = None
    created_at: str = ""
    updated_at: str = ""


def _row_to_model(row: dict[str, Any]) -> MessageLedgerRow:
    return MessageLedgerRow(
        trace_id=str(row["trace_id"]),
        session_id=str(row["session_id"]),
        channel=str(row["channel"]),
        input_text=str(row["input_text"]),
        chat_id=str(row["chat_id"]) if row.get("chat_id") is not None else None,
        status=str(row["status"]),
        failure_reason=(
            str(row["failure_reason"]) if row.get("failure_reason") is not None else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


_SELECT_COLUMNS = (
    "trace_id, session_id, channel, chat_id, input_text, status, failure_reason, "
    "created_at, updated_at"
)


class MessageLedgerStore(OwnedRepository):
    """Async SQLite wrapper for the message_ledger table (migration 0089).

    Mirrors the established Store shape (:class:`~stackowl.memory.retry_queue_store.RetryQueueStore`):
    hand-rolled SQL via ``self._db.execute``/``fetch_all`` with an explicit
    ``owner_id = ?`` bind on every query.
    """

    _table = "message_ledger"

    def __init__(self, db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        super().__init__(db, owner_id)
        log.memory.debug(
            "message_ledger_store.init: ready",
            extra={"_fields": {"owner_id": self._owner_id}},
        )

    async def insert_pending(
        self, *, trace_id: str, session_id: str, channel: str, input_text: str,
        chat_id: int | str | None = None,
    ) -> None:
        """Insert a new pending row. Idempotent: a duplicate trace_id is a no-op.

        ``INSERT OR IGNORE`` on the ``trace_id`` primary key — a redispatch
        that reuses the same trace_id (e.g. a retry replay) must not error.
        ``chat_id`` is the fan-out delivery target (e.g. a Telegram chat_id,
        mirrors ``IngressMessage.chat_id``/``PipelineState.reply_target``),
        stringified for the TEXT column; None for single-terminal channels.
        """
        # 1. ENTRY
        log.memory.debug(
            "message_ledger_store.insert_pending: entry",
            extra={"_fields": {
                "trace_id": trace_id, "session_id": session_id, "channel": channel,
            }},
        )
        # 2. DECISION — truncate free-text input, same rule as retry_queue.goal.
        truncated_input = input_text[:_INPUT_TEXT_MAX_LEN]
        chat_id_str = str(chat_id) if chat_id is not None else None
        now = _now_iso()
        try:
            # 3. STEP
            await self._db.execute(
                """INSERT OR IGNORE INTO message_ledger
                   (trace_id, session_id, channel, chat_id, input_text, status,
                    failure_reason, owner_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', NULL, ?, ?, ?)""",
                (
                    trace_id, session_id, channel, chat_id_str, truncated_input,
                    self._owner_id, now, now,
                ),
            )
        except Exception as exc:
            log.memory.error(
                "message_ledger_store.insert_pending: insert failed",
                exc_info=exc,
                extra={"_fields": {"trace_id": trace_id, "session_id": session_id}},
            )
            raise
        # 4. EXIT
        log.memory.debug(
            "message_ledger_store.insert_pending: exit",
            extra={"_fields": {"trace_id": trace_id}},
        )

    async def _flip(self, trace_id: str, *, status: str, failure_reason: str | None) -> bool:
        """Shared CAS flip: pending -> a terminal status. Returns whether it won.

        Scoped to ``WHERE status = 'pending'`` so a redundant flip (e.g. the
        safety-net done-callback firing after persist_turn already flipped
        the row) is a harmless no-op, not a double-write.
        """
        truncated_reason = failure_reason[:_FAILURE_REASON_MAX_LEN] if failure_reason else None
        try:
            affected = await self._db.execute_returning_rowcount(
                "UPDATE message_ledger SET status = ?, failure_reason = ?, updated_at = ? "
                "WHERE trace_id = ? AND owner_id = ? AND status = 'pending'",
                (status, truncated_reason, _now_iso(), trace_id, self._owner_id),
            )
        except Exception as exc:
            log.memory.error(
                "message_ledger_store._flip: update failed",
                exc_info=exc,
                extra={"_fields": {"trace_id": trace_id, "status": status}},
            )
            raise
        won = affected > 0
        if not won:
            log.memory.debug(
                "message_ledger_store._flip: no matching pending row — wrong trace_id, "
                "wrong owner, or already flipped",
                extra={"_fields": {"trace_id": trace_id, "status": status}},
            )
        return won

    async def mark_completed(self, trace_id: str) -> bool:
        """Mark a row ``status = 'completed'`` — a reply was delivered."""
        log.memory.debug(
            "message_ledger_store.mark_completed: entry",
            extra={"_fields": {"trace_id": trace_id}},
        )
        won = await self._flip(trace_id, status="completed", failure_reason=None)
        log.memory.info(
            "message_ledger_store.mark_completed: exit",
            extra={"_fields": {"trace_id": trace_id, "won": won}},
        )
        return won

    async def mark_failed(self, trace_id: str, *, reason: str) -> bool:
        """Mark a row ``status = 'failed'`` — the turn floored or was dropped."""
        log.memory.debug(
            "message_ledger_store.mark_failed: entry",
            extra={"_fields": {"trace_id": trace_id}},
        )
        won = await self._flip(trace_id, status="failed", failure_reason=reason)
        log.memory.info(
            "message_ledger_store.mark_failed: exit",
            extra={"_fields": {"trace_id": trace_id, "won": won, "reason": reason}},
        )
        return won

    async def mark_absorbed(self, trace_id: str) -> bool:
        """Mark a row ``status = 'absorbed'`` — folded (STEER) into another turn."""
        log.memory.debug(
            "message_ledger_store.mark_absorbed: entry",
            extra={"_fields": {"trace_id": trace_id}},
        )
        won = await self._flip(trace_id, status="absorbed", failure_reason=None)
        log.memory.info(
            "message_ledger_store.mark_absorbed: exit",
            extra={"_fields": {"trace_id": trace_id, "won": won}},
        )
        return won

    async def get_pending(self, *, limit: int = 500) -> list[MessageLedgerRow]:
        """Return pending rows for this owner, oldest first — for boot recovery.

        No age filter: at boot the prior process is dead by definition (same
        reasoning ``recover_durable_tasks`` uses for orphaned rows), so every
        pending row is a candidate for redrive.
        """
        # 1. ENTRY
        log.memory.debug(
            "message_ledger_store.get_pending: entry",
            extra={"_fields": {"limit": limit}},
        )
        if limit < 1:
            log.memory.error(
                "message_ledger_store.get_pending: non-positive limit rejected",
                extra={"_fields": {"limit": limit}},
            )
            raise ValueError(f"limit must be >= 1, got {limit}")
        rows = await self._db.fetch_all(
            f"""SELECT {_SELECT_COLUMNS} FROM message_ledger
                WHERE owner_id = ? AND status = 'pending'
                ORDER BY created_at ASC LIMIT ?""",
            (self._owner_id, limit),
        )
        # 3. STEP — project rows
        results = [_row_to_model(r) for r in rows]
        # 4. EXIT
        log.memory.debug(
            "message_ledger_store.get_pending: exit",
            extra={"_fields": {"n_pending": len(results)}},
        )
        return results
