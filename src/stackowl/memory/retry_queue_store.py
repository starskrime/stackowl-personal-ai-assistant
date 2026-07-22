"""RetryQueueStore — persistence for the failure retry loop.

Every floored turn (the terminal "I couldn't fully complete this" response)
gets a row here, inserted synchronously in-pipeline (turn_persist.py) and
backfilled with the sent channel message reference asynchronously once the
Telegram send resolves (adapter.py). A scheduler sweep (retry_sweep.py)
retries due rows every minute, re-arming on an exponential (capped) delay
forever — no attempt cap, no terminal give-up (owner decision 2026-07-22).

Owner-scoped (subclasses :class:`~stackowl.tenancy.OwnedRepository`): every
query binds ``owner_id = ?`` so a row can never be read or written across
principals. Wraps migration 0082's ``retry_queue`` table — see
``src/stackowl/db/migrations/0082_retry_queue.sql`` for the shipped schema.
This store is deliberately NOT wired into any caller yet (Stories 1.3-1.7).

Cross-instance races (two sweep workers polling ``get_due`` concurrently, or
a duplicate ``trace_id`` reaching ``insert_pending``) are pre-existing schema
gaps tracked in ``deferred-work.md`` against migration 0082 (no claimed-state,
no ``trace_id`` uniqueness) — out of scope here. Within-process races on a
single row (concurrent ``mark_attempt_failed`` calls) ARE this store's
responsibility and are closed via :meth:`DbPool.transaction`.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID, OwnedRepository

_RETRY_INTERVAL_MINUTES = 1
#: FX-02 — a fixed 1-minute re-arm hammers a still-failing goal at the same
#: cadence regardless of how many times it's already failed. Doubling per
#: attempt (1, 2, 4, ... capped) spaces later attempts out. No terminal
#: attempt cap (owner decision 2026-07-22) — a row now re-arms forever
#: instead of being abandoned as permanently "failed"; this cap only paces
#: how often a chronically-failing row is retried, it never gives up on it.
_RETRY_INTERVAL_CAP_MINUTES = 10
#: Unbounded exception text must not bloat the row (Boundaries & Constraints).
_LAST_ERROR_MAX_LEN = 2000
#: Same concern as last_error — goal is free-text and must not bloat the row.
_GOAL_MAX_LEN = 4000


def _retry_delay_minutes(attempt_count: int) -> float:
    """Exponential re-arm delay for the Nth failed attempt, capped.

    ``attempt_count`` is unbounded now that a row never terminally fails, so
    the exponent is clamped before ``2.0 ** exponent`` — otherwise a row that
    has failed thousands of times over a long period would eventually
    overflow float exponentiation. 20 is already far past the point
    ``_RETRY_INTERVAL_CAP_MINUTES`` takes over, so clamping there changes
    nothing observable.
    """
    exponent = min(attempt_count - 1, 20)
    delay = float(_RETRY_INTERVAL_MINUTES) * (2.0**exponent)
    return min(delay, float(_RETRY_INTERVAL_CAP_MINUTES))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class RetryQueueRow:
    """Read-side projection of one retry_queue row."""

    id: str
    trace_id: str
    session_id: str
    goal: str
    banned_capabilities: list[str] = field(default_factory=list)
    attempt_count: int = 0
    status: str = "pending"
    next_retry_at: str = ""
    last_error: str | None = None
    channel: str = "telegram"
    channel_chat_id: str | None = None
    channel_message_id: str | None = None
    created_at: str = ""
    updated_at: str = ""


def _row_to_model(row: dict[str, Any]) -> RetryQueueRow:
    """Project a raw ``retry_queue`` row dict onto :class:`RetryQueueRow`.

    ``banned_capabilities`` round-trips through the column as JSON text
    (migration 0082's ``TEXT NOT NULL DEFAULT '[]'``) — deserialized here so
    every caller sees a Python list, never a JSON string. Every write path in
    this store writes valid JSON, but the column has no `json_valid()` CHECK
    (deferred-work item against migration 0082), so a corrupted value reaching
    this point fails loud with a clear error instead of a bare JSONDecodeError.
    """
    try:
        banned_capabilities = json.loads(str(row["banned_capabilities"]))
    except json.JSONDecodeError as exc:
        log.memory.error(
            "retry_queue_store._row_to_model: corrupted banned_capabilities JSON",
            exc_info=exc,
            extra={"_fields": {"retry_id": str(row.get("id", "?"))}},
        )
        raise ValueError(
            f"retry_queue row {row.get('id', '?')!r} has corrupted banned_capabilities JSON"
        ) from exc
    return RetryQueueRow(
        id=str(row["id"]),
        trace_id=str(row["trace_id"]),
        session_id=str(row["session_id"]),
        goal=str(row["goal"]),
        banned_capabilities=banned_capabilities,
        attempt_count=int(row["attempt_count"]),
        status=str(row["status"]),
        next_retry_at=str(row["next_retry_at"]),
        last_error=str(row["last_error"]) if row.get("last_error") is not None else None,
        channel=str(row["channel"]),
        channel_chat_id=(
            str(row["channel_chat_id"]) if row.get("channel_chat_id") is not None else None
        ),
        channel_message_id=(
            str(row["channel_message_id"])
            if row.get("channel_message_id") is not None
            else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _cursor_row_to_dict(cursor: Any, raw: Any) -> dict[str, Any]:
    """Project one ``aiosqlite`` cursor row onto a plain dict, mirroring DbPool.fetch_all."""
    keys = [d[0] for d in cursor.description]
    return dict(zip(keys, tuple(raw), strict=False))


_SELECT_COLUMNS = (
    "id, trace_id, session_id, goal, banned_capabilities, attempt_count, "
    "status, next_retry_at, last_error, channel, channel_chat_id, "
    "channel_message_id, created_at, updated_at"
)


class RetryQueueStore(OwnedRepository):
    """Async SQLite wrapper for the retry_queue table (migration 0082).

    Mirrors the established Store shape (:class:`~stackowl.memory.outcome_store.TaskOutcomeStore`):
    hand-rolled SQL via ``self._db.execute``/``fetch_all`` with an explicit
    ``owner_id = ?`` bind on every query, not the ``OwnedRepository``
    ``_insert_owned``/``_fetch_owned``/`_update_owned`` helpers.
    """

    _table = "retry_queue"

    def __init__(self, db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        super().__init__(db, owner_id)
        log.memory.debug(
            "retry_queue_store.init: ready",
            extra={"_fields": {"owner_id": self._owner_id}},
        )

    async def insert_pending(
        self,
        *,
        trace_id: str,
        session_id: str,
        goal: str,
        banned_capabilities: list[str],
        channel: str = "telegram",
    ) -> str:
        """Insert a new pending row (``attempt_count=0``, due immediately).

        Returns the app-generated UUID hex id (migration 0082's ``id`` is an
        app-generated TEXT PRIMARY KEY, not an autoincrement column).
        """
        # 1. ENTRY
        log.memory.debug(
            "retry_queue_store.insert_pending: entry",
            extra={"_fields": {
                "trace_id": trace_id, "session_id": session_id, "channel": channel,
                "n_banned": len(banned_capabilities),
            }},
        )
        # 2. DECISION — mint the row id and stamp "due now" so a fresh row is
        # immediately visible to get_due() (Story 1.1's schema has no separate
        # "not yet due" state — a pending row is due the moment it's created).
        # goal is truncated for the same reason last_error is (free-text column,
        # must not bloat the row on an unbounded input).
        retry_id = uuid.uuid4().hex
        now = _now_iso()
        truncated_goal = goal[:_GOAL_MAX_LEN]
        log.memory.debug(
            "retry_queue_store.insert_pending: minted id, due now",
            extra={"_fields": {"retry_id": retry_id, "next_retry_at": now}},
        )
        try:
            # 3. STEP
            await self._db.execute(
                """INSERT INTO retry_queue
                   (id, trace_id, session_id, goal, banned_capabilities, attempt_count,
                    status, next_retry_at, last_error, channel, channel_chat_id,
                    channel_message_id, owner_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 0, 'pending', ?, NULL, ?, NULL, NULL, ?, ?, ?)""",
                (
                    retry_id, trace_id, session_id, truncated_goal,
                    json.dumps(banned_capabilities, separators=(",", ":")),
                    now, channel, self._owner_id, now, now,
                ),
            )
        except Exception as exc:
            log.memory.error(
                "retry_queue_store.insert_pending: insert failed",
                exc_info=exc,
                extra={"_fields": {"trace_id": trace_id, "session_id": session_id}},
            )
            raise
        # 4. EXIT
        log.memory.info(
            "retry_queue_store.insert_pending: exit",
            extra={"_fields": {"retry_id": retry_id, "trace_id": trace_id}},
        )
        return retry_id

    async def supersede(
        self,
        retry_id: str,
        *,
        trace_id: str,
        goal: str,
        banned_capabilities: list[str],
    ) -> None:
        """Repoint an existing PENDING row at a newer floored turn.

        Used when a session already has a pending retry and a SECOND turn
        floors before the first fires (``turn_persist.py``'s one-pending-per-
        session dedup — see its 2026-07-16 incident comment): rather than
        silently dropping the newer ask, this keeps the one-row-per-session
        invariant that fix relies on while still tracking the user's LATEST
        request. Resets ``attempt_count``/``banned_capabilities``/``last_error``
        (this is effectively a fresh ask, not another failure of the old one)
        and re-arms ``next_retry_at`` to due-now, mirroring :meth:`insert_pending`.
        Scoped to ``status = 'pending'`` — a row that already resolved must not
        be repointed.
        """
        # 1. ENTRY
        log.memory.debug(
            "retry_queue_store.supersede: entry",
            extra={"_fields": {
                "retry_id": retry_id, "trace_id": trace_id, "n_banned": len(banned_capabilities),
            }},
        )
        now = _now_iso()
        truncated_goal = goal[:_GOAL_MAX_LEN]
        try:
            # 3. STEP
            affected = await self._db.execute_returning_rowcount(
                """UPDATE retry_queue
                   SET trace_id = ?, goal = ?, banned_capabilities = ?, attempt_count = 0,
                       status = 'pending', next_retry_at = ?, last_error = NULL, updated_at = ?
                   WHERE id = ? AND owner_id = ? AND status = 'pending'""",
                (
                    trace_id, truncated_goal,
                    json.dumps(banned_capabilities, separators=(",", ":")),
                    now, now, retry_id, self._owner_id,
                ),
            )
        except Exception as exc:
            log.memory.error(
                "retry_queue_store.supersede: update failed",
                exc_info=exc,
                extra={"_fields": {"retry_id": retry_id, "trace_id": trace_id}},
            )
            raise
        # 2. DECISION + 4. EXIT
        if affected == 0:
            log.memory.warning(
                "retry_queue_store.supersede: no matching pending row — wrong id, "
                "wrong owner, or row already advanced",
                extra={"_fields": {"retry_id": retry_id}},
            )
            return
        log.memory.info(
            "retry_queue_store.supersede: exit",
            extra={"_fields": {"retry_id": retry_id, "trace_id": trace_id}},
        )

    async def backfill_channel_message(
        self, *, trace_id: str, channel_chat_id: int, channel_message_id: int,
    ) -> None:
        """Stamp the sent channel message reference onto the pending row for ``trace_id``.

        Scoped to ``status = 'pending'`` — a row that already completed/failed
        (e.g. a fast retry beat the async backfill) must not be touched.
        migration 0082 has no UNIQUE constraint on ``trace_id`` (deferred-work
        item), so this selects the single most-recent matching pending row
        (inside one transaction) and updates it by ``id`` — bounding the blast
        radius to one row even if a duplicate ``trace_id`` exists, instead of
        an unscoped ``UPDATE ... WHERE trace_id = ?`` stamping every match.
        """
        # 1. ENTRY
        log.memory.debug(
            "retry_queue_store.backfill_channel_message: entry",
            extra={"_fields": {
                "trace_id": trace_id, "channel_chat_id": channel_chat_id,
                "channel_message_id": channel_message_id,
            }},
        )
        # 2. DECISION — stringify ints for the TEXT columns (matches migration 0082).
        chat_id_str = str(channel_chat_id)
        message_id_str = str(channel_message_id)
        now = _now_iso()
        try:
            # 3. STEP — select-then-update the single target row, atomically.
            async with self._db.transaction() as conn:
                cursor = await conn.execute(
                    """SELECT id FROM retry_queue
                       WHERE trace_id = ? AND owner_id = ? AND status = 'pending'
                       ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                    (trace_id, self._owner_id),
                )
                raw = await cursor.fetchone()
                if raw is None:
                    log.memory.warning(
                        "retry_queue_store.backfill_channel_message: no matching pending row",
                        extra={"_fields": {"trace_id": trace_id}},
                    )
                    return
                target_id = raw[0]
                await conn.execute(
                    """UPDATE retry_queue SET channel_chat_id = ?, channel_message_id = ?,
                       updated_at = ? WHERE id = ? AND owner_id = ?""",
                    (chat_id_str, message_id_str, now, target_id, self._owner_id),
                )
        except Exception as exc:
            log.memory.error(
                "retry_queue_store.backfill_channel_message: update failed",
                exc_info=exc,
                extra={"_fields": {"trace_id": trace_id}},
            )
            raise
        # 4. EXIT
        log.memory.debug(
            "retry_queue_store.backfill_channel_message: exit",
            extra={"_fields": {"trace_id": trace_id}},
        )

    async def get_due(self, *, limit: int = 25) -> list[RetryQueueRow]:
        """Return pending rows whose ``next_retry_at`` has passed, earliest-due first."""
        # 1. ENTRY
        log.memory.debug(
            "retry_queue_store.get_due: entry",
            extra={"_fields": {"limit": limit}},
        )
        # 2. DECISION — "due" means pending AND next_retry_at <= now; uses
        # idx_retry_queue_status_due (migration 0082). Reject non-positive
        # limits: SQLite treats a negative LIMIT as unbounded, defeating the
        # batch cap this parameter exists to enforce.
        if limit < 1:
            log.memory.error(
                "retry_queue_store.get_due: non-positive limit rejected",
                extra={"_fields": {"limit": limit}},
            )
            raise ValueError(f"limit must be >= 1, got {limit}")
        now = _now_iso()
        rows = await self._db.fetch_all(
            f"""SELECT {_SELECT_COLUMNS} FROM retry_queue
                WHERE owner_id = ? AND status = 'pending' AND next_retry_at <= ?
                ORDER BY next_retry_at ASC LIMIT ?""",
            (self._owner_id, now, limit),
        )
        # 3. STEP — project rows
        results = [_row_to_model(r) for r in rows]
        # 4. EXIT
        log.memory.debug(
            "retry_queue_store.get_due: exit",
            extra={"_fields": {"limit": limit, "n_due": len(results)}},
        )
        return results

    async def get_latest_pending_for_session(self, session_id: str) -> RetryQueueRow | None:
        """Return the most recently created pending row for ``session_id``, or None."""
        # 1. ENTRY
        log.memory.debug(
            "retry_queue_store.get_latest_pending_for_session: entry",
            extra={"_fields": {"session_id": session_id}},
        )
        # 3. STEP — rowid DESC breaks ties when two rows share the same
        # created_at timestamp (same-millisecond concurrent inserts).
        rows = await self._db.fetch_all(
            f"""SELECT {_SELECT_COLUMNS} FROM retry_queue
                WHERE owner_id = ? AND session_id = ? AND status = 'pending'
                ORDER BY created_at DESC, rowid DESC LIMIT 1""",
            (self._owner_id, session_id),
        )
        # 2. DECISION + 4. EXIT
        if not rows:
            log.memory.debug(
                "retry_queue_store.get_latest_pending_for_session: exit — miss",
                extra={"_fields": {"session_id": session_id}},
            )
            return None
        result = _row_to_model(rows[0])
        log.memory.debug(
            "retry_queue_store.get_latest_pending_for_session: exit — hit",
            extra={"_fields": {"session_id": session_id, "retry_id": result.id}},
        )
        return result

    async def mark_completed(self, retry_id: str) -> None:
        """Mark a row ``status = 'completed'`` — the retry succeeded.

        Logs a warning (not an exception — callers don't need a new failure
        mode) when no row matched: wrong id, wrong owner, or the row already
        moved on. Silence there would otherwise hide a caller bug.
        """
        # 1. ENTRY
        log.memory.debug(
            "retry_queue_store.mark_completed: entry",
            extra={"_fields": {"retry_id": retry_id}},
        )
        try:
            # 3. STEP
            affected = await self._db.execute_returning_rowcount(
                "UPDATE retry_queue SET status = 'completed', updated_at = ? "
                "WHERE id = ? AND owner_id = ?",
                (_now_iso(), retry_id, self._owner_id),
            )
        except Exception as exc:
            log.memory.error(
                "retry_queue_store.mark_completed: update failed",
                exc_info=exc,
                extra={"_fields": {"retry_id": retry_id}},
            )
            raise
        # 2. DECISION + 4. EXIT
        if affected == 0:
            log.memory.warning(
                "retry_queue_store.mark_completed: no matching row — wrong id, "
                "wrong owner, or row already advanced",
                extra={"_fields": {"retry_id": retry_id}},
            )
            return
        log.memory.info(
            "retry_queue_store.mark_completed: exit",
            extra={"_fields": {"retry_id": retry_id}},
        )

    async def reschedule(self, retry_id: str, *, delay_seconds: float, error: str) -> None:
        """Push a pending row's ``next_retry_at`` out by ``delay_seconds``,
        WITHOUT touching ``attempt_count``/``status``/``banned_capabilities``.

        Used when a retry's ANSWER was computed fine but DELIVERY itself
        failed for a reason unrelated to which capability the model chose
        (e.g. a Telegram ``RetryAfter`` flood-control error) — re-arming on
        the fixed 1-minute cadence :meth:`mark_attempt_failed` uses would keep
        hammering a channel that is already rate-limited, extending the ban
        instead of waiting it out. ``delay_seconds`` should come from the
        transport error itself when available.
        """
        # 1. ENTRY
        log.memory.debug(
            "retry_queue_store.reschedule: entry",
            extra={"_fields": {"retry_id": retry_id, "delay_seconds": delay_seconds}},
        )
        next_retry_at = (datetime.now(UTC) + timedelta(seconds=delay_seconds)).isoformat()
        truncated_error = error[:_LAST_ERROR_MAX_LEN]
        try:
            # 3. STEP
            affected = await self._db.execute_returning_rowcount(
                "UPDATE retry_queue SET next_retry_at = ?, last_error = ?, updated_at = ? "
                "WHERE id = ? AND owner_id = ? AND status = 'pending'",
                (next_retry_at, truncated_error, _now_iso(), retry_id, self._owner_id),
            )
        except Exception as exc:
            log.memory.error(
                "retry_queue_store.reschedule: update failed",
                exc_info=exc,
                extra={"_fields": {"retry_id": retry_id}},
            )
            raise
        # 2. DECISION + 4. EXIT
        if affected == 0:
            log.memory.warning(
                "retry_queue_store.reschedule: no matching pending row — wrong id, "
                "wrong owner, or row already advanced",
                extra={"_fields": {"retry_id": retry_id}},
            )
            return
        log.memory.info(
            "retry_queue_store.reschedule: exit",
            extra={"_fields": {"retry_id": retry_id, "next_retry_at": next_retry_at}},
        )

    async def mark_attempt_failed(
        self, *, retry_id: str, newly_failed_capability: str, error: str,
    ) -> RetryQueueRow:
        """Record a failed retry attempt: increment, ban the capability, re-arm.

        Read-then-write inside one :meth:`DbPool.transaction` (SELECT current
        row, compute next state, UPDATE, return the computed row) — the whole
        unit runs under the pool's write lock so two concurrent calls for the
        same ``retry_id`` (e.g. overlapping sweep ticks) cannot both read the
        same ``attempt_count`` and silently lose an increment. Raises
        :class:`ValueError` if ``retry_id`` doesn't exist for this owner, or if
        the row is not currently ``pending`` (already terminal via
        :meth:`mark_completed`, or raced by a concurrent caller — a caller
        bug, since a terminal row should never be retried again).

        No attempt cap (owner decision 2026-07-22): a row always re-arms and
        stays ``pending`` — it is never abandoned as permanently "failed" no
        matter how many times it has failed. Only the retry PACING grows
        (see :func:`_retry_delay_minutes`), never a give-up.
        """
        # 1. ENTRY
        log.memory.debug(
            "retry_queue_store.mark_attempt_failed: entry",
            extra={"_fields": {
                "retry_id": retry_id, "newly_failed_capability": newly_failed_capability,
            }},
        )
        truncated_error = error[:_LAST_ERROR_MAX_LEN]
        try:
            # 3. STEP — read-compute-write as one atomic unit.
            async with self._db.transaction() as conn:
                cursor = await conn.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM retry_queue WHERE id = ? AND owner_id = ?",
                    (retry_id, self._owner_id),
                )
                raw = await cursor.fetchone()
                if raw is None:
                    log.memory.error(
                        "retry_queue_store.mark_attempt_failed: row not found",
                        extra={"_fields": {"retry_id": retry_id}},
                    )
                    raise ValueError(f"retry_queue row not found: {retry_id}")
                current = _row_to_model(_cursor_row_to_dict(cursor, raw))

                # 2. DECISION — refuse to re-fail an already-terminal row (a
                # caller bug — a completed/raced row should never be retried
                # again); dedup the newly-failed capability; ALWAYS re-arm on
                # an exponential (capped) delay — see _retry_delay_minutes.
                # No terminal "failed" status: a row keeps re-arming no matter
                # how many times it fails (owner decision 2026-07-22).
                if current.status != "pending":
                    log.memory.error(
                        "retry_queue_store.mark_attempt_failed: row not pending",
                        extra={"_fields": {"retry_id": retry_id, "status": current.status}},
                    )
                    raise ValueError(
                        f"retry_queue row {retry_id} is not pending "
                        f"(status={current.status!r}) — cannot mark another attempt failed"
                    )
                banned = [*current.banned_capabilities]
                if newly_failed_capability and newly_failed_capability not in banned:
                    banned.append(newly_failed_capability)
                attempt_count = current.attempt_count + 1
                status = "pending"
                next_retry_at = (
                    datetime.now(UTC)
                    + timedelta(minutes=_retry_delay_minutes(attempt_count))
                ).isoformat()
                now = _now_iso()
                log.memory.debug(
                    "retry_queue_store.mark_attempt_failed: decision",
                    extra={"_fields": {"retry_id": retry_id, "attempt_count": attempt_count}},
                )

                # 3. STEP — write the computed next state
                await conn.execute(
                    """UPDATE retry_queue
                       SET banned_capabilities = ?, attempt_count = ?, status = ?,
                           next_retry_at = ?, last_error = ?, updated_at = ?
                       WHERE id = ? AND owner_id = ?""",
                    (
                        json.dumps(banned, separators=(",", ":")), attempt_count, status,
                        next_retry_at, truncated_error, now, retry_id, self._owner_id,
                    ),
                )
        except ValueError:
            raise
        except Exception as exc:
            log.memory.error(
                "retry_queue_store.mark_attempt_failed: transaction failed",
                exc_info=exc,
                extra={"_fields": {"retry_id": retry_id}},
            )
            raise

        result = RetryQueueRow(
            id=current.id, trace_id=current.trace_id, session_id=current.session_id,
            goal=current.goal, banned_capabilities=banned, attempt_count=attempt_count,
            status=status, next_retry_at=next_retry_at, last_error=truncated_error,
            channel=current.channel, channel_chat_id=current.channel_chat_id,
            channel_message_id=current.channel_message_id, created_at=current.created_at,
            updated_at=now,
        )
        # 4. EXIT
        log.memory.warning(
            "retry_queue_store.mark_attempt_failed: exit",
            extra={"_fields": {
                "retry_id": retry_id, "attempt_count": attempt_count, "status": status,
                "newly_failed_capability": newly_failed_capability,
            }},
        )
        return result
