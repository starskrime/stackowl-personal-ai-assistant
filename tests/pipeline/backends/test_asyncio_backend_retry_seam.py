"""Backend-level seam test for the failure retry loop (final whole-branch
review findings C1 + C2).

Drives a FULL ``AsyncioBackend.run()`` call — not a step-level unit test —
with a pending ``retry_queue`` row and an inbound message the (mocked)
``RetryIntentClassifier`` confirms as retry-intent, using the SAME backend
instance for both the outer (real user) turn and ``RetryActuator``'s nested
re-run, exactly as ``startup/orchestrator.py`` wires it in production.

Without the fix this test either recurses (triage keeps re-detecting
retry-intent on the still-"pending" row and re-dispatching ``attempt_retry``,
whose nested ``backend.run()`` hits triage again — C2) or double-delivers
(the remaining pipeline steps + the delivery gate + ``deliver.run`` still run
after the actuator already sent the answer — C1).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.memory.retry_queue_store import RetryQueueRow
from stackowl.pipeline.backends import asyncio_backend as backend_mod
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.retry_actuator import RetryActuator
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import triage


def _row() -> RetryQueueRow:
    return RetryQueueRow(
        id="retry-1", trace_id="trace-orig", session_id="sess-1",
        goal="prepare me for the interview", banned_capabilities=[],
        attempt_count=0, status="pending", next_retry_at="", last_error=None,
        channel="telegram", channel_chat_id="555", channel_message_id="999",
        created_at="", updated_at="",
    )


@pytest.mark.asyncio
async def test_manual_retry_seam_single_delivery_no_recursion(monkeypatch) -> None:
    row = _row()

    retry_store = MagicMock()
    retry_store.get_latest_pending_for_session = AsyncMock(return_value=row)
    retry_store.insert_pending = AsyncMock()
    retry_store.mark_completed = AsyncMock()
    retry_store.mark_attempt_failed = AsyncMock()

    classifier = MagicMock()
    classifier.classify = AsyncMock(return_value=True)

    adapter = MagicMock()
    adapter.edit_message = AsyncMock()
    channel_registry = MagicMock()
    channel_registry.get = MagicMock(return_value=adapter)

    services = StepServices(
        retry_queue_store=retry_store,
        retry_intent_classifier=classifier,
    )
    backend = AsyncioBackend(services=services)
    # Wires the SAME backend instance into RetryActuator that triage.py will
    # dispatch through — matching startup/orchestrator.py's production wiring
    # (RetryActuator(backend=backend, ...), then injected back onto services).
    services.retry_actuator = RetryActuator(
        backend=backend, channel_registry=channel_registry, retry_store=retry_store,
    )

    # Only the real triage step is exercised — sufficient to prove the seam:
    # a retry_dispatched outer turn must never reach the rest of the pipeline
    # (C1), and the inner synthetic re-run's triage call must never re-detect
    # retry-intent (C2). Downstream steps would be reached only if either
    # guard failed.
    monkeypatch.setattr(backend_mod, "PIPELINE_STEPS", [("triage", triage.run)])
    deliver_calls: list[str] = []

    async def _spy_deliver(state: PipelineState) -> PipelineState:
        deliver_calls.append(state.trace_id)
        return state

    monkeypatch.setattr(backend_mod.deliver, "run", _spy_deliver)

    outer_state = PipelineState(
        trace_id="outer-1", session_id="sess-1", input_text="do it again",
        channel="telegram", owl_name="secretary", pipeline_step="",
        interactive=True, reply_target=555,
    )
    final_state = await backend.run(outer_state)

    # C1 — triage dispatched the retry and the outer turn short-circuited:
    # exactly one pipeline step ran (triage), and the pipeline's OWN deliver
    # step never fired for the outer turn (no second response).
    assert final_state.retry_dispatched is True
    assert [name for name, _ in final_state.step_durations] == ["triage"]
    assert "outer-1" not in deliver_calls

    # C1 — the actuator delivered the real answer exactly once (edit_message,
    # since row.channel_chat_id + channel_message_id are both set).
    adapter.edit_message.assert_awaited_once()
    adapter.send_text.assert_not_called()

    # C2 — the nested backend.run() inside attempt_retry (interactive=False)
    # never re-entered the retry-intent hook: the pending-row lookup and the
    # classifier were each consulted exactly ONCE (the outer turn only), so
    # attempt_retry was never dispatched a second time / no recursion.
    retry_store.get_latest_pending_for_session.assert_awaited_once()
    classifier.classify.assert_awaited_once()
