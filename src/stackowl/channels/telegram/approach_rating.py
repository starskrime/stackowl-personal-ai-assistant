"""Like/Dislike approach-rating buttons — CallbackRouter prefix "apr".

Mirrors consent.py's pattern: build a keyboard, track the sent message's
(chat_id, message_id) in an in-memory map keyed by trace_id (backfilled
post-send, same convention command_buttons.py uses), edit the message in
place on tap. Unlike consent.py this is fire-and-forget (no blocking
Future/park) — a vote is a one-shot write, nothing waits on it.
"""

from __future__ import annotations

from typing import Any, Protocol

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


class _SupportsEditMessage(Protocol):
    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: Any | None = None,
    ) -> bool: ...

    async def answer_callback_query(self, callback_id: str) -> Any: ...


class ApproachRatingTracker:
    """In-memory trace_id -> (chat_id, message_id) map for pending votes."""

    def __init__(self) -> None:
        self._pending: dict[str, tuple[int, int] | None] = {}

    def record_pending(self, *, trace_id: str) -> None:
        self._pending[trace_id] = None

    def backfill_message(self, *, trace_id: str, chat_id: int, message_id: int) -> None:
        if trace_id in self._pending:
            self._pending[trace_id] = (chat_id, message_id)

    def get_message(self, *, trace_id: str) -> tuple[int, int] | None:
        return self._pending.get(trace_id)

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
            await self._safe_answer(callback_id)
            return

        updated = await self._outcome_store.set_approach_rating(trace_id=trace_id, rating=vote)
        await self._safe_answer(callback_id)
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
        chat_id, message_id = location
        suffix = _LIKED_SUFFIX if vote == "positive" else _DISLIKED_SUFFIX
        try:
            await self._adapter.edit_message(chat_id, message_id, suffix, reply_markup=None)
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

    async def _safe_answer(self, callback_id: str) -> None:
        try:
            await self._adapter.answer_callback_query(callback_id)
        except Exception as exc:
            log.telegram.error(
                "approach_rating.handle: answer_callback_query failed",
                exc_info=exc, extra={"_fields": {"callback_id": callback_id}},
            )
