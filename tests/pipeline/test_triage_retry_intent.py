"""Tests for triage's Task 7 manual "do it again" retry-intent hook.

The hook must run BEFORE any normal routing (direct-address / sticky-cache /
SecretaryRouter) so a retry-intent message dispatches RetryActuator
immediately instead of falling through to a full routing pass.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.memory.retry_queue_store import RetryQueueRow
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState


def _row() -> RetryQueueRow:
    return RetryQueueRow(
        id="r1",
        trace_id="t1",
        session_key="s1",
        goal="prepare me for the interview",
        banned_capabilities=[],
        attempt_count=0,
        status="pending",
        next_retry_at="",
        last_error=None,
        channel="telegram",
        channel_chat_id="1",
        channel_message_id="2",
        created_at="",
        updated_at="",
    )


def _task(status: str = "failed"):
    """A task the loop has GIVEN UP on — the only kind "try again" acts on.

    A floored turn is requeued `pending` and the loop re-drives it itself; a
    manual retry of that row would be a second engine racing the first."""
    from types import SimpleNamespace

    return SimpleNamespace(
        task_id="task-r1",
        session_key="s1",
        goal="prepare me for the interview",
        banned_capabilities=[],
        attempt_count=3,
        last_error="the turn delivered an honest floor instead of the work",
        channel="telegram",
        destination="telegram:1",
        status=status,
        created_at="2026-09-03T00:00:00+00:00",
    )


def _services(*, retry_store, classifier, actuator) -> StepServices:
    return StepServices(
        durable_task_store=retry_store,
        retry_intent_classifier=classifier,
        retry_actuator=actuator,
    )


@pytest.mark.asyncio
async def test_triage_triggers_manual_retry():
    from stackowl.pipeline.steps import triage

    retry_store = MagicMock()
    retry_store.latest_abandoned_for_session = AsyncMock(return_value=_task())

    classifier = MagicMock()
    classifier.classify = AsyncMock(return_value=True)

    actuator = MagicMock()
    actuator.attempt_retry = AsyncMock()

    token = set_services(
        _services(retry_store=retry_store, classifier=classifier, actuator=actuator)
    )
    try:
        state = PipelineState(
            trace_id="t2", session_key="s1", input_text="do it again",
            channel="telegram", owl_name="secretary", pipeline_step="triage",
            interactive=True,  # C2 fix — hook is gated on a real (interactive) user turn
        )
        result = await triage.run(state)
    finally:
        reset_services(token)

    actuator.attempt_retry.assert_awaited_once()
    classifier.classify.assert_awaited_once_with(
        user_message="do it again", prior_goal="prepare me for the interview",
    )
    assert result.retry_dispatched is True


@pytest.mark.asyncio
async def test_triage_no_pending_row_falls_through_to_normal_routing():
    from stackowl.pipeline.steps import triage

    retry_store = MagicMock()
    retry_store.latest_abandoned_for_session = AsyncMock(return_value=None)

    classifier = MagicMock()
    classifier.classify = AsyncMock(return_value=True)

    actuator = MagicMock()
    actuator.attempt_retry = AsyncMock()

    token = set_services(
        _services(retry_store=retry_store, classifier=classifier, actuator=actuator)
    )
    try:
        # owl_name != secretary -> direct-address path; no owl_registry wired
        # so it accepts as-is and returns quickly without touching the router.
        state = PipelineState(
            trace_id="t3", session_key="s1", input_text="what's the weather",
            channel="telegram", owl_name="max", pipeline_step="triage",
        )
        result = await triage.run(state)
    finally:
        reset_services(token)

    classifier.classify.assert_not_awaited()
    actuator.attempt_retry.assert_not_awaited()
    assert result.retry_dispatched is False
    assert result.owl_name == "max"


@pytest.mark.asyncio
async def test_triage_pending_row_but_not_retry_intent_falls_through():
    from stackowl.pipeline.steps import triage

    retry_store = MagicMock()
    retry_store.latest_abandoned_for_session = AsyncMock(return_value=_task())

    classifier = MagicMock()
    classifier.classify = AsyncMock(return_value=False)

    actuator = MagicMock()
    actuator.attempt_retry = AsyncMock()

    token = set_services(
        _services(retry_store=retry_store, classifier=classifier, actuator=actuator)
    )
    try:
        state = PipelineState(
            trace_id="t4", session_key="s1", input_text="what's the weather",
            channel="telegram", owl_name="max", pipeline_step="triage",
            interactive=True,  # C2 fix — hook is gated on a real (interactive) user turn
        )
        result = await triage.run(state)
    finally:
        reset_services(token)

    classifier.classify.assert_awaited_once()
    actuator.attempt_retry.assert_not_awaited()
    assert result.retry_dispatched is False


@pytest.mark.asyncio
async def test_triage_no_retry_store_is_noop():
    """No retry_queue_store wired (existing tests / earlier deploy) -> byte-identical no-op."""
    from stackowl.pipeline.steps import triage

    token = set_services(StepServices())
    try:
        state = PipelineState(
            trace_id="t5", session_key="s1", input_text="do it again",
            channel="telegram", owl_name="max", pipeline_step="triage",
        )
        result = await triage.run(state)
    finally:
        reset_services(token)

    assert result.retry_dispatched is False


@pytest.mark.asyncio
async def test_a_PENDING_task_is_not_offered_for_manual_retry():
    """The loop owns `pending`; the operator owns give-up.

    A floored turn is requeued as `pending` by ``complete_turn_task`` and the loop
    re-drives it on its own while it still has attempts. Dispatching a manual
    retry of that same row would be a SECOND engine running the same work and
    racing the first — the thing Bakir's 2026-08-17 rule forbids. The store query
    is what enforces it, so this asserts on the statuses that query accepts."""
    from stackowl.pipeline.durable.store import DurableTaskStore

    assert "pending" not in DurableTaskStore.ABANDONED_STATUSES
    assert "running" not in DurableTaskStore.ABANDONED_STATUSES
    assert set(DurableTaskStore.ABANDONED_STATUSES) == {"failed", "dead_letter"}


@pytest.mark.asyncio
async def test_the_retry_carries_the_learning_the_loop_PAID_FOR():
    """`banned_capabilities` is what the loop bought with its failed attempts.
    Dropping it sends the manual retry back down a route already proven dead —
    which is why triage and the loop share ONE row builder rather than each
    writing their own."""
    from stackowl.pipeline.durable.task_loop_runner import actuator_row_for

    task = _task()
    task.banned_capabilities = ["web_fetch", "shell"]

    row = actuator_row_for(task)

    assert list(row.banned_capabilities) == ["web_fetch", "shell"]
    assert row.attempt_count == 3, "the attempt history was reset to zero"
    assert row.goal == "prepare me for the interview"
    assert row.channel_chat_id == "1", "the destination was lost, so the answer has nowhere to go"
