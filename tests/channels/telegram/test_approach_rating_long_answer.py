"""A Like on a LONG answer must still visibly register.

REPORTED LIVE 2026-08-07: "i did press like on telegram message was not liked
and buttons still on screen."

What actually happened, from the logs:

    20:35:28  [outcomes] set_approach_rating: exit          <- the vote WAS stored
    20:35:29  adapter.edit_message: edit failed — fail open
              telegram.error.BadRequest: Message_too_long
    20:35:39  set_approach_rating: exit                     <- he tapped again
    20:35:39  approach_rating.handle: vote recorded but no
              message location — edit skipped

The acknowledgement is removing the keyboard, and it was being done with
``edit_message_text`` — a FULL-TEXT replace that Telegram caps at 4096
characters. So it failed on exactly the answers most worth rating (a
``headhunter`` reply carrying web-search results), the buttons stayed put, and
the vote looked lost even though it was already in ``task_outcomes``.

The second tap then recorded a DUPLICATE vote and found no message location,
because the tracker is cleared whether or not the edit succeeded.
"""

from __future__ import annotations

import pytest

from stackowl.channels.telegram.approach_rating import ApproachRatingCallbackHandler


class _TooLongOnTextEdit:
    """Telegram's real behaviour: a text edit over 4096 chars is rejected, while
    a markup-only edit has no length limit at all."""

    LIMIT = 4096

    def __init__(self) -> None:
        self.text_edits: list[int] = []
        self.markup_removals: list[int] = []

    async def edit_message(self, chat_id, message_id, text, *, reply_markup=None):  # noqa: ANN001, ANN003
        self.text_edits.append(len(text))
        if len(text) > self.LIMIT:
            raise RuntimeError("BadRequest: Message_too_long")
        return True

    async def remove_message_buttons(self, chat_id, message_id) -> bool:  # noqa: ANN001
        self.markup_removals.append(message_id)
        return True


class _Store:
    def __init__(self) -> None:
        self.ratings: list[tuple[str, str]] = []

    async def set_approach_rating(self, *, trace_id: str, rating: str) -> bool:
        self.ratings.append((trace_id, rating))
        return True


class _Tracker:
    def __init__(self, text: str) -> None:
        self._text = text
        self.cleared: list[str] = []

    async def get_message(self, *, trace_id: str):  # noqa: ANN202
        return (72055773, 13881, self._text)

    async def clear(self, *, trace_id: str) -> None:
        self.cleared.append(trace_id)


def _handler(adapter, store, tracker):  # noqa: ANN001, ANN202
    h = ApproachRatingCallbackHandler.__new__(ApproachRatingCallbackHandler)
    h._adapter = adapter          # noqa: SLF001
    h._outcome_store = store      # noqa: SLF001
    h._tracker = tracker          # noqa: SLF001
    return h


_LONG = "x" * 9000   # a real headhunter answer with search results
_SHORT = "a short reply"


@pytest.mark.asyncio
async def test_a_long_answer_still_loses_its_buttons():
    """THE REPORTED BUG. The text edit cannot apply, so the keyboard must be
    dropped on its own — otherwise the tap leaves no visible trace and the user
    reasonably concludes it did not work."""
    adapter = _TooLongOnTextEdit()
    handler = _handler(adapter, _Store(), _Tracker(_LONG))

    await handler.handle("cb-1", "rate:trace-1:positive")

    assert adapter.text_edits, "the text edit should still be attempted first"
    assert adapter.markup_removals == [13881], (
        "the keyboard MUST be removed when the text edit fails — that removal is "
        "the entire acknowledgement the user sees"
    )


@pytest.mark.asyncio
async def test_a_short_answer_keeps_the_nicer_behaviour():
    """The suffix is the nicety and it must not be lost: when the text fits, the
    answer is edited to carry the 'Liked' marker and no fallback is needed."""
    adapter = _TooLongOnTextEdit()
    handler = _handler(adapter, _Store(), _Tracker(_SHORT))

    await handler.handle("cb-2", "rate:trace-2:positive")

    assert adapter.text_edits, "short answers still get the suffix edit"
    assert adapter.markup_removals == [], "no fallback needed when the edit works"


@pytest.mark.asyncio
async def test_the_vote_is_recorded_even_when_the_edit_fails():
    """It always was — that is why this looked like a UI bug rather than a lost
    vote. Pinned so a future 'fix' cannot start dropping the vote instead."""
    store = _Store()
    handler = _handler(_TooLongOnTextEdit(), store, _Tracker(_LONG))

    await handler.handle("cb-3", "rate:trace-3:negative")

    assert store.ratings == [("trace-3", "negative")]


@pytest.mark.asyncio
async def test_the_tracker_is_cleared_exactly_once():
    """The second tap in the live report hit 'no message location' because the
    tracker had been cleared by the first. That is correct one-shot behaviour —
    what was wrong was needing a second tap at all."""
    tracker = _Tracker(_LONG)
    handler = _handler(_TooLongOnTextEdit(), _Store(), tracker)

    await handler.handle("cb-4", "rate:trace-4:positive")

    assert tracker.cleared == ["trace-4"]
