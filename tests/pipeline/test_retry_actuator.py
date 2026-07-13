from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.memory.retry_queue_store import RetryQueueRow
from stackowl.pipeline.retry_actuator import RetryActuator
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _row(**overrides):
    defaults = dict(
        id="retry-1", trace_id="trace-orig", session_id="sess-1",
        goal="prepare me for the interview", banned_capabilities=["cronjob"],
        attempt_count=0, status="pending", next_retry_at="", last_error=None,
        channel="telegram", channel_chat_id="555", channel_message_id="999",
        created_at="", updated_at="",
    )
    defaults.update(overrides)
    return RetryQueueRow(**defaults)


@pytest.mark.asyncio
async def test_attempt_retry_success_edits_message():
    row = _row()

    success_state = PipelineState(
        trace_id="trace-new", session_id="sess-1", input_text=row.goal,
        channel="telegram", owl_name="secretary", pipeline_step="",
        responses=(
            ResponseChunk(
                content="Here's your interview prep plan...", is_final=True,
                chunk_index=0, trace_id="trace-new", owl_name="secretary", is_floor=False,
            ),
        ),
    )
    backend = MagicMock()
    backend.run = AsyncMock(return_value=success_state)

    adapter = MagicMock()
    adapter.edit_message = AsyncMock()
    channel_registry = MagicMock()
    channel_registry.get = MagicMock(return_value=adapter)

    retry_store = MagicMock()
    retry_store.mark_completed = AsyncMock()

    actuator = RetryActuator(backend=backend, channel_registry=channel_registry, retry_store=retry_store)
    outcome = await actuator.attempt_retry(row)

    assert outcome.status == "completed"
    adapter.edit_message.assert_awaited_once()
    retry_store.mark_completed.assert_awaited_once_with("retry-1")

    # banned capability must have been injected into the re-run prompt
    call_state = backend.run.await_args.args[0]
    assert "cronjob" in call_state.input_text


@pytest.mark.asyncio
async def test_attempt_retry_failure_marks_attempt():
    row = _row()

    floored_state = PipelineState(
        trace_id="trace-new", session_id="sess-1", input_text=row.goal,
        channel="telegram", owl_name="secretary", pipeline_step="",
        responses=(
            ResponseChunk(
                content="I still couldn't...", is_final=False, chunk_index=0,
                trace_id="trace-new", owl_name="secretary", is_floor=True,
            ),
        ),
    )
    backend = MagicMock()
    backend.run = AsyncMock(return_value=floored_state)

    channel_registry = MagicMock()
    retry_store = MagicMock()
    updated_row = _row(attempt_count=1, status="pending")
    retry_store.mark_attempt_failed = AsyncMock(return_value=updated_row)

    actuator = RetryActuator(backend=backend, channel_registry=channel_registry, retry_store=retry_store)
    outcome = await actuator.attempt_retry(row)

    assert outcome.status == "pending"
    retry_store.mark_attempt_failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_attempt_retry_pins_newly_failed_capability_not_already_banned():
    """Pins the _pick_newly_failed fix: a genuine new consequential failure from
    this retry attempt (not already in row.banned_capabilities) must be the
    exact value threaded to mark_attempt_failed — not "" (the no-op result a
    broken/reverted version would also produce for an empty-ledger fixture)."""
    row = _row(banned_capabilities=["cronjob"])

    floored_state = PipelineState(
        trace_id="trace-new", session_id="sess-1", input_text=row.goal,
        channel="telegram", owl_name="secretary", pipeline_step="",
        consequential_snapshot_taken=True,
        consequential_failures=("web_search",),
        responses=(
            ResponseChunk(
                content="I still couldn't...", is_final=False, chunk_index=0,
                trace_id="trace-new", owl_name="secretary", is_floor=True,
            ),
        ),
    )
    backend = MagicMock()
    backend.run = AsyncMock(return_value=floored_state)

    channel_registry = MagicMock()
    retry_store = MagicMock()
    updated_row = _row(attempt_count=1, status="pending")
    retry_store.mark_attempt_failed = AsyncMock(return_value=updated_row)

    actuator = RetryActuator(backend=backend, channel_registry=channel_registry, retry_store=retry_store)
    outcome = await actuator.attempt_retry(row)

    assert outcome.status == "pending"
    retry_store.mark_attempt_failed.assert_awaited_once()
    assert (
        retry_store.mark_attempt_failed.await_args.kwargs["newly_failed_capability"]
        == "web_search"
    )


@pytest.mark.asyncio
async def test_attempt_retry_budget_capped_partial_is_not_success():
    """A retry attempt that hits the SAME budget cap again produces a non-floored
    response chunk (execute.py's default-backstop branch never sets is_floor=True
    on a non-empty partial) but DOES stamp budget_capped=True on the final state.
    That must route through _handle_failure (mark_attempt_failed), NOT be treated
    as a genuine success (_deliver_success/mark_completed) — otherwise a
    recurring scheduled objective that always hits the same cap gets its
    half-finished partial delivered to the user as if it were done, forever."""
    row = _row()

    budget_capped_state = PipelineState(
        trace_id="trace-new", session_id="sess-1", input_text=row.goal,
        channel="telegram", owl_name="secretary", pipeline_step="",
        budget_capped=True,
        responses=(
            ResponseChunk(
                content="Found multiple stories... let me get the details.",
                is_final=False, chunk_index=0,
                trace_id="trace-new", owl_name="secretary", is_floor=False,
            ),
        ),
    )
    backend = MagicMock()
    backend.run = AsyncMock(return_value=budget_capped_state)

    adapter = MagicMock()
    adapter.edit_message = AsyncMock()
    channel_registry = MagicMock()
    channel_registry.get = MagicMock(return_value=adapter)

    retry_store = MagicMock()
    retry_store.mark_completed = AsyncMock()
    updated_row = _row(attempt_count=1, status="pending")
    retry_store.mark_attempt_failed = AsyncMock(return_value=updated_row)

    actuator = RetryActuator(backend=backend, channel_registry=channel_registry, retry_store=retry_store)
    outcome = await actuator.attempt_retry(row)

    assert outcome.status == "pending"
    retry_store.mark_attempt_failed.assert_awaited_once()
    retry_store.mark_completed.assert_not_awaited()
    adapter.edit_message.assert_not_awaited()
    adapter.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_attempt_retry_survives_channel_registry_error_on_give_up():
    """_notify_gave_up's channel_registry.get() call can raise (e.g.
    ChannelNotFoundError for an unregistered channel) — this must be caught
    inside _notify_gave_up's own try block, not propagate out of
    attempt_retry, which promises to never raise into the scheduler loop."""
    row = _row()

    floored_state = PipelineState(
        trace_id="trace-new", session_id="sess-1", input_text=row.goal,
        channel="telegram", owl_name="secretary", pipeline_step="",
        responses=(
            ResponseChunk(
                content="I still couldn't...", is_final=False, chunk_index=0,
                trace_id="trace-new", owl_name="secretary", is_floor=True,
            ),
        ),
    )
    backend = MagicMock()
    backend.run = AsyncMock(return_value=floored_state)

    channel_registry = MagicMock()
    channel_registry.get = MagicMock(side_effect=Exception("channel gone"))

    retry_store = MagicMock()
    updated_row = _row(attempt_count=3, status="failed")
    retry_store.mark_attempt_failed = AsyncMock(return_value=updated_row)

    actuator = RetryActuator(backend=backend, channel_registry=channel_registry, retry_store=retry_store)
    outcome = await actuator.attempt_retry(row)

    assert outcome.status == "failed"
