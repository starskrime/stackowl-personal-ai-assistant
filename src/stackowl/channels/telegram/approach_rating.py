"""Like/Dislike approach-rating buttons — CallbackRouter prefix "apr".

Mirrors consent.py's pattern: build a keyboard, track the sent message's
(chat_id, message_id) keyed by trace_id (backfilled post-send, same convention
command_buttons.py uses), edit the message in place on tap. Unlike consent.py
this is fire-and-forget (no blocking Future/park) — a vote is a one-shot
write, nothing waits on it.

DB-backed (migration 0084), NOT in-memory: this codebase runs a genuine
two-process split (``runtime.split_process`` — see orchestrator.py's
``role``-gated ``_phase_gateway``). ``record_pending`` runs in the CORE
process (consolidate.py, a pipeline step); ``backfill_message`` and the
callback tap's ``get_message``/``clear`` run in the GATEWAY process (the
Telegram adapter + the registered "apr" callback handler are only constructed
when ``role != "core"``). An in-memory dict built once per process (the
original implementation) meant gateway and core each held their OWN separate
map, so a tapped vote recorded correctly in ``task_outcomes`` but the gateway
side never saw the pending entry — the message was never edited. Both
processes share the same SQLite DB file, so a DB-backed store (mirroring
:class:`~stackowl.memory.retry_queue_store.RetryQueueStore`) is correct where
the in-memory dict was not.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from stackowl.channels.telegram.keyboard import InlineKeyboardBuilder
from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID, OwnedRepository

__all__ = [
    "APPROACH_RATING_PREFIX",
    "ApproachRatingCallbackHandler",
    "ApproachRatingTracker",
]

APPROACH_RATING_PREFIX = "apr"

_LIKE_LABEL = "\U0001F44D"
_DISLIKE_LABEL = "\U0001F44E"
_LIKED_SUFFIX = "\n\n\U0001F44D Liked"
_DISLIKED_SUFFIX = "\n\n\U0001F44E Disliked"


class _SupportsEditMessage(Protocol):
    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: Any | None = None,
    ) -> bool: ...


class ApproachRatingTracker(OwnedRepository):
    """DB-backed trace_id -> pending-vote store (migration 0084's
    ``approach_rating_pending`` table).

    Mirrors the established Store shape (:class:`~stackowl.memory.retry_queue_store.RetryQueueStore`):
    hand-rolled SQL via ``self._db.execute``/``fetch_all`` with an explicit
    ``owner_id = ?`` bind on every query.

    # ponytail: no size cap / TTL sweep — a DB row is small (short text +
    # two ints) and rare (one per qualifying Telegram answer, cleared on
    # tap), so an unbounded table is fine for now. Add a periodic
    # delete-older-than-N-days sweep if untapped votes ever accumulate.
    """

    _table = "approach_rating_pending"

    def __init__(self, db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        super().__init__(db, owner_id)
        log.telegram.debug(
            "approach_rating.tracker.init: ready",
            extra={"_fields": {"owner_id": self._owner_id}},
        )

    async def record_pending(self, *, trace_id: str, text: str) -> None:
        """Record the original answer text for ``trace_id``, awaiting a tap.

        Upsert on ``trace_id`` (the PRIMARY KEY): a re-recorded trace_id resets
        chat_id/message_id to NULL along with the text, matching the old
        in-memory ``dict[trace_id] = _PendingVote(text=text)`` overwrite
        semantics exactly.
        """
        # 1. ENTRY
        log.telegram.debug(
            "approach_rating.record_pending: entry",
            extra={"_fields": {"trace_id": trace_id, "text_len": len(text)}},
        )
        try:
            # 3. STEP
            await self._db.execute(
                """INSERT INTO approach_rating_pending
                       (trace_id, owner_id, text, chat_id, message_id, created_at)
                   VALUES (?, ?, ?, NULL, NULL, ?)
                   ON CONFLICT(trace_id) DO UPDATE SET
                       owner_id = excluded.owner_id, text = excluded.text,
                       chat_id = NULL, message_id = NULL, created_at = excluded.created_at""",
                (trace_id, self._owner_id, text, time.time()),
            )
        except Exception as exc:
            log.telegram.error(
                "approach_rating.record_pending: insert failed",
                exc_info=exc, extra={"_fields": {"trace_id": trace_id}},
            )
            raise
        # 4. EXIT
        log.telegram.debug(
            "approach_rating.record_pending: exit",
            extra={"_fields": {"trace_id": trace_id}},
        )

    async def backfill_message(self, *, trace_id: str, chat_id: int, message_id: int) -> None:
        """Stamp the just-sent message ref onto the pending row for ``trace_id``.

        No-op (logged) if no matching row exists — the row may have already
        been cleared by a fast vote tap, or never existed for this owner.
        """
        # 1. ENTRY
        log.telegram.debug(
            "approach_rating.backfill_message: entry",
            extra={"_fields": {
                "trace_id": trace_id, "chat_id": chat_id, "message_id": message_id,
            }},
        )
        try:
            # 3. STEP
            affected = await self._db.execute_returning_rowcount(
                "UPDATE approach_rating_pending SET chat_id = ?, message_id = ? "
                "WHERE trace_id = ? AND owner_id = ?",
                (chat_id, message_id, trace_id, self._owner_id),
            )
        except Exception as exc:
            log.telegram.error(
                "approach_rating.backfill_message: update failed",
                exc_info=exc, extra={"_fields": {"trace_id": trace_id}},
            )
            raise
        # 4. EXIT
        if affected == 0:
            log.telegram.debug(
                "approach_rating.backfill_message: exit — no matching pending row",
                extra={"_fields": {"trace_id": trace_id}},
            )
        else:
            log.telegram.debug(
                "approach_rating.backfill_message: exit",
                extra={"_fields": {"trace_id": trace_id}},
            )

    async def get_message(self, *, trace_id: str) -> tuple[int, int, str] | None:
        """Return ``(chat_id, message_id, original_text)`` for ``trace_id``,
        or None if no row exists or the message ref hasn't been backfilled yet."""
        # 1. ENTRY
        log.telegram.debug(
            "approach_rating.get_message: entry",
            extra={"_fields": {"trace_id": trace_id}},
        )
        rows = await self._db.fetch_all(
            "SELECT chat_id, message_id, text FROM approach_rating_pending "
            "WHERE trace_id = ? AND owner_id = ?",
            (trace_id, self._owner_id),
        )
        # 2. DECISION + 4. EXIT
        if not rows:
            log.telegram.debug(
                "approach_rating.get_message: exit — miss",
                extra={"_fields": {"trace_id": trace_id}},
            )
            return None
        row = rows[0]
        chat_id, message_id = row.get("chat_id"), row.get("message_id")
        if chat_id is None or message_id is None:
            log.telegram.debug(
                "approach_rating.get_message: exit — no message location yet",
                extra={"_fields": {"trace_id": trace_id}},
            )
            return None
        result = (int(chat_id), int(message_id), str(row.get("text") or ""))
        log.telegram.debug(
            "approach_rating.get_message: exit — hit",
            extra={"_fields": {"trace_id": trace_id}},
        )
        return result

    async def clear(self, *, trace_id: str) -> None:
        """Delete the pending row for ``trace_id`` (no-op if none exists)."""
        # 1. ENTRY
        log.telegram.debug(
            "approach_rating.clear: entry",
            extra={"_fields": {"trace_id": trace_id}},
        )
        try:
            # 3. STEP
            await self._db.execute(
                "DELETE FROM approach_rating_pending WHERE trace_id = ? AND owner_id = ?",
                (trace_id, self._owner_id),
            )
        except Exception as exc:
            log.telegram.error(
                "approach_rating.clear: delete failed",
                exc_info=exc, extra={"_fields": {"trace_id": trace_id}},
            )
            raise
        # 4. EXIT
        log.telegram.debug(
            "approach_rating.clear: exit",
            extra={"_fields": {"trace_id": trace_id}},
        )

    def build_keyboard(self, *, trace_id: str) -> dict[str, object]:
        """Pure keyboard-dict construction — no state lookup, stays synchronous."""
        builder = InlineKeyboardBuilder()
        builder.add_button(_LIKE_LABEL, f"{APPROACH_RATING_PREFIX}:{trace_id}:positive")
        builder.add_button(_DISLIKE_LABEL, f"{APPROACH_RATING_PREFIX}:{trace_id}:negative")
        return builder.build()


class ApproachRatingCallbackHandler:
    """CallbackRouter handler for the "apr" prefix."""

    def __init__(
        self,
        *,
        tracker: ApproachRatingTracker,
        outcome_store: TaskOutcomeStore,
        adapter: _SupportsEditMessage,
    ) -> None:
        self._tracker = tracker
        self._outcome_store = outcome_store
        self._adapter = adapter

    async def handle(self, callback_id: str, callback_data: str) -> None:
        # 1. ENTRY
        log.telegram.debug(
            "approach_rating.handle: entry",
            extra={"_fields": {"callback_data": callback_data}},
        )
        try:
            _, trace_id, vote = callback_data.split(":", 2)
        except ValueError:
            log.telegram.error(
                "approach_rating.handle: malformed callback_data",
                extra={"_fields": {"callback_data": callback_data}},
            )
            return

        if vote not in ("positive", "negative"):
            log.telegram.error(
                "approach_rating.handle: invalid vote value — rejecting before DB write",
                extra={"_fields": {"trace_id": trace_id, "vote": vote}},
            )
            await self._tracker.clear(trace_id=trace_id)
            return

        try:
            updated = await self._outcome_store.set_approach_rating(trace_id=trace_id, rating=vote)
        except Exception as exc:
            log.telegram.error(
                "approach_rating.handle: set_approach_rating failed",
                exc_info=exc, extra={"_fields": {"trace_id": trace_id}},
            )
            await self._tracker.clear(trace_id=trace_id)
            return

        if not updated:
            log.telegram.warning(
                "approach_rating.handle: no task_outcomes row for trace — vote recorded nowhere",
                extra={"_fields": {"trace_id": trace_id}},
            )
            await self._tracker.clear(trace_id=trace_id)
            return

        location = await self._tracker.get_message(trace_id=trace_id)
        if location is None:
            log.telegram.warning(
                "approach_rating.handle: vote recorded but no message location — edit skipped",
                extra={"_fields": {"trace_id": trace_id}},
            )
            return
        chat_id, message_id, original_text = location
        suffix = _LIKED_SUFFIX if vote == "positive" else _DISLIKED_SUFFIX
        try:
            # Append to the ORIGINAL answer text — edit_message is a full-text
            # replace (edit_message_text), never an append, so reconstructing
            # from the stored text is required or the tap destroys the answer.
            await self._adapter.edit_message(
                chat_id, message_id, f"{original_text}{suffix}", reply_markup=None
            )
        except Exception as exc:  # message may be too old/deleted — vote already recorded, don't fail the turn
            log.telegram.error(
                "approach_rating.handle: edit failed — vote already recorded",
                exc_info=exc, extra={"_fields": {"trace_id": trace_id}},
            )
        finally:
            await self._tracker.clear(trace_id=trace_id)
        # 4. EXIT
        log.telegram.info(
            "approach_rating.handle: exit",
            extra={"_fields": {"trace_id": trace_id, "vote": vote}},
        )
