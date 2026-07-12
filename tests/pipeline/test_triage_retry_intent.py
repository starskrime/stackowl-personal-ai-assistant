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
        session_id="s1",
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


def _services(*, retry_store, classifier, actuator) -> StepServices:
    return StepServices(
        retry_queue_store=retry_store,
        retry_intent_classifier=classifier,
        retry_actuator=actuator,
    )


@pytest.mark.asyncio
async def test_triage_triggers_manual_retry():
    from stackowl.pipeline.steps import triage

    retry_store = MagicMock()
    retry_store.get_latest_pending_for_session = AsyncMock(return_value=_row())

    classifier = MagicMock()
    classifier.classify = AsyncMock(return_value=True)

    actuator = MagicMock()
    actuator.attempt_retry = AsyncMock()

    token = set_services(
        _services(retry_store=retry_store, classifier=classifier, actuator=actuator)
    )
    try:
        state = PipelineState(
            trace_id="t2", session_id="s1", input_text="do it again",
            channel="telegram", owl_name="secretary", pipeline_step="triage",
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
    retry_store.get_latest_pending_for_session = AsyncMock(return_value=None)

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
            trace_id="t3", session_id="s1", input_text="what's the weather",
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
    retry_store.get_latest_pending_for_session = AsyncMock(return_value=_row())

    classifier = MagicMock()
    classifier.classify = AsyncMock(return_value=False)

    actuator = MagicMock()
    actuator.attempt_retry = AsyncMock()

    token = set_services(
        _services(retry_store=retry_store, classifier=classifier, actuator=actuator)
    )
    try:
        state = PipelineState(
            trace_id="t4", session_id="s1", input_text="what's the weather",
            channel="telegram", owl_name="max", pipeline_step="triage",
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
            trace_id="t5", session_id="s1", input_text="do it again",
            channel="telegram", owl_name="max", pipeline_step="triage",
        )
        result = await triage.run(state)
    finally:
        reset_services(token)

    assert result.retry_dispatched is False
