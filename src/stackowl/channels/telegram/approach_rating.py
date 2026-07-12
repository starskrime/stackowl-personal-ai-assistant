"""Like/Dislike approach-rating buttons — CallbackRouter prefix "apr".

Mirrors consent.py's pattern: build a keyboard, track the sent message's
(chat_id, message_id) in an in-memory map keyed by trace_id (backfilled
post-send, same convention command_buttons.py uses), edit the message in
place on tap. Unlike consent.py this is fire-and-forget (no blocking
Future/park) — a vote is a one-shot write, nothing waits on it.
"""

from __future__ import annotations

from typing import Any, NamedTuple, Protocol

from stackowl.channels.telegram.keyboard import InlineKeyboardBuilder
from stackowl.infra.observability import log
from stackowl.memory.outcome_store import TaskOutcomeStore

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

# Bound on the tracker's in-memory map so an untapped vote (or a non-Telegram
# turn, pre channel-gate) can never grow it forever in a long-lived gateway
# process — this is ephemeral state anyway (a restart clears it), so a simple
# insert-time eviction of the oldest entry is proportionate.
# ponytail: no TTL/background sweep — a size cap is enough for ephemeral state.
_MAX_PENDING = 500


class _PendingVote(NamedTuple):
    """One pending vote's tracked state: the original answer text (known at
    ``record_pending`` time) plus the (chat_id, message_id) backfilled once
    the message is actually sent."""

    text: str
    chat_id: int | None = None
    message_id: int | None = None


class _SupportsEditMessage(Protocol):
    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: Any | None = None,
    ) -> bool: ...


class ApproachRatingTracker:
    """In-memory trace_id -> pending-vote map, size-capped (see ``_MAX_PENDING``)."""

    def __init__(self) -> None:
        self._pending: dict[str, _PendingVote] = {}

    def record_pending(self, *, trace_id: str, text: str) -> None:
        # Regular dicts preserve insertion order — evict the oldest entry
        # before inserting a new one once at capacity, so an untapped vote
        # (or repeated qualifying turns) can never grow this map unbounded.
        if trace_id not in self._pending and len(self._pending) >= _MAX_PENDING:
            oldest_trace_id = next(iter(self._pending))
            del self._pending[oldest_trace_id]
            log.telegram.warning(
                "approach_rating.record_pending: cap reached — evicted oldest entry",
                extra={"_fields": {"evicted_trace_id": oldest_trace_id, "cap": _MAX_PENDING}},
            )
        self._pending[trace_id] = _PendingVote(text=text)

    def backfill_message(self, *, trace_id: str, chat_id: int, message_id: int) -> None:
        entry = self._pending.get(trace_id)
        if entry is not None:
            self._pending[trace_id] = entry._replace(chat_id=chat_id, message_id=message_id)

    def get_message(self, *, trace_id: str) -> tuple[int, int, str] | None:
        entry = self._pending.get(trace_id)
        if entry is None or entry.chat_id is None or entry.message_id is None:
            return None
        return (entry.chat_id, entry.message_id, entry.text)

    def clear(self, *, trace_id: str) -> None:
        self._pending.pop(trace_id, None)

    def build_keyboard(self, *, trace_id: str) -> dict[str, object]:
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
            self._tracker.clear(trace_id=trace_id)
            return

        try:
            updated = await self._outcome_store.set_approach_rating(trace_id=trace_id, rating=vote)
        except Exception as exc:
            log.telegram.error(
                "approach_rating.handle: set_approach_rating failed",
                exc_info=exc, extra={"_fields": {"trace_id": trace_id}},
            )
            self._tracker.clear(trace_id=trace_id)
            return

        if not updated:
            log.telegram.warning(
                "approach_rating.handle: no task_outcomes row for trace — vote recorded nowhere",
                extra={"_fields": {"trace_id": trace_id}},
            )
            self._tracker.clear(trace_id=trace_id)
            return

        location = self._tracker.get_message(trace_id=trace_id)
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
            self._tracker.clear(trace_id=trace_id)
        # 4. EXIT
        log.telegram.info(
            "approach_rating.handle: exit",
            extra={"_fields": {"trace_id": trace_id, "vote": vote}},
        )
