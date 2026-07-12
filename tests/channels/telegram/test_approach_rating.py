from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.channels.telegram.approach_rating import (
    ApproachRatingCallbackHandler,
    ApproachRatingTracker,
)


def test_build_keyboard_has_two_buttons():
    tracker = ApproachRatingTracker()
    keyboard = tracker.build_keyboard(trace_id="trace-1")

    buttons = keyboard["inline_keyboard"][0]
    assert len(buttons) == 2
    assert buttons[0]["callback_data"] == "apr:trace-1:positive"
    assert buttons[1]["callback_data"] == "apr:trace-1:negative"


def test_backfill_then_lookup():
    tracker = ApproachRatingTracker()
    tracker.record_pending(trace_id="trace-1")
    tracker.backfill_message(trace_id="trace-1", chat_id=555, message_id=999)

    assert tracker.get_message(trace_id="trace-1") == (555, 999)


@pytest.mark.asyncio
async def test_handle_positive_vote_records_and_edits():
    tracker = ApproachRatingTracker()
    tracker.record_pending(trace_id="trace-1")
    tracker.backfill_message(trace_id="trace-1", chat_id=555, message_id=999)

    outcome_store = MagicMock()
    outcome_store.set_approach_rating = AsyncMock(return_value=True)

    adapter = MagicMock()
    adapter.edit_message = AsyncMock()

    handler = ApproachRatingCallbackHandler(tracker=tracker, outcome_store=outcome_store, adapter=adapter)
    await handler.handle("callback-id-1", "apr:trace-1:positive")

    outcome_store.set_approach_rating.assert_awaited_once_with(trace_id="trace-1", rating="positive")
    adapter.edit_message.assert_awaited_once()
    call_args = adapter.edit_message.await_args
    assert call_args.args[0] == 555
    assert call_args.args[1] == 999


@pytest.mark.asyncio
async def test_handle_unknown_trace_id_noops_gracefully():
    tracker = ApproachRatingTracker()
    outcome_store = MagicMock()
    outcome_store.set_approach_rating = AsyncMock(return_value=False)
    adapter = MagicMock()
    adapter.edit_message = AsyncMock()

    handler = ApproachRatingCallbackHandler(tracker=tracker, outcome_store=outcome_store, adapter=adapter)
    await handler.handle("callback-id-2", "apr:unknown-trace:positive")  # must not raise

    adapter.edit_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_invalid_vote_rejected_before_db_write():
    tracker = ApproachRatingTracker()
    tracker.record_pending(trace_id="trace-1")

    outcome_store = MagicMock()
    outcome_store.set_approach_rating = AsyncMock(return_value=True)

    adapter = MagicMock()
    adapter.edit_message = AsyncMock()

    handler = ApproachRatingCallbackHandler(tracker=tracker, outcome_store=outcome_store, adapter=adapter)
    await handler.handle("callback-id-3", "apr:trace-1:sideways")  # not "positive"/"negative"

    outcome_store.set_approach_rating.assert_not_awaited()
    adapter.edit_message.assert_not_awaited()
    assert tracker.get_message(trace_id="trace-1") is None


@pytest.mark.asyncio
async def test_handle_store_exception_still_clears_tracker():
    tracker = ApproachRatingTracker()
    tracker.record_pending(trace_id="trace-1")
    tracker.backfill_message(trace_id="trace-1", chat_id=555, message_id=999)

    outcome_store = MagicMock()
    outcome_store.set_approach_rating = AsyncMock(side_effect=RuntimeError("db down"))

    adapter = MagicMock()
    adapter.edit_message = AsyncMock()

    handler = ApproachRatingCallbackHandler(tracker=tracker, outcome_store=outcome_store, adapter=adapter)
    await handler.handle("callback-id-4", "apr:trace-1:positive")  # must not raise

    adapter.edit_message.assert_not_awaited()
    # tracker entry must not leak on a store failure
    assert "trace-1" not in tracker._pending
