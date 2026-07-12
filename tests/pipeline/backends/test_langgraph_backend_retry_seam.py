"""LangGraphBackend counterpart to test_asyncio_backend_retry_seam.py (final
whole-branch review findings C1 + C2).

The graph is built from ``PIPELINE_STEPS`` at construction time (mirrors
test_langgraph_backend_durable_scope.py's pattern), so ``PIPELINE_STEPS`` is
patched to just the real ``triage`` step BEFORE instantiating the backend.
``use_memory_checkpoint=True`` keeps the test hermetic (no sqlite).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.memory.retry_queue_store import RetryQueueRow
from stackowl.pipeline.backends.langgraph_backend import LangGraphBackend
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


async def test_manual_retry_seam_single_delivery_no_recursion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackowl.pipeline.backends.langgraph_backend as mod

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

    # PIPELINE_STEPS must be patched BEFORE construction — _build_graph_builder
    # reads it in __init__. Only the real triage step is needed: it proves the
    # conditional edge added for C1 (triage -> END on retry_dispatched, never
    # reaching "deliver") without needing the rest of the graph wired up.
    monkeypatch.setattr(mod, "PIPELINE_STEPS", [("triage", triage.run)])
    backend = LangGraphBackend(services=services, use_memory_checkpoint=True)
    services.retry_actuator = RetryActuator(
        backend=backend, channel_registry=channel_registry, retry_store=retry_store,
    )

    deliver_calls: list[str] = []

    async def _spy_deliver(state: PipelineState) -> PipelineState:
        deliver_calls.append(state.trace_id)
        return state

    monkeypatch.setattr(mod.deliver, "run", _spy_deliver)

    outer_state = PipelineState(
        trace_id="outer-1", session_id="sess-1", input_text="do it again",
        channel="telegram", owl_name="secretary", pipeline_step="",
        interactive=True, reply_target=555,
    )
    try:
        final_state = await backend.run(outer_state)
    finally:
        await backend.shutdown()

    # C1 — the conditional edge routed triage straight to END: the "deliver"
    # node (which runs the delivery gate + deliver.run) never fired for the
    # outer turn — no second response.
    assert final_state.retry_dispatched is True
    assert "outer-1" not in deliver_calls

    # C1 — the actuator delivered the real answer exactly once.
    adapter.edit_message.assert_awaited_once()
    adapter.send_text.assert_not_called()

    # C2 — the nested backend.run() inside attempt_retry (interactive=False)
    # never re-entered the retry-intent hook.
    retry_store.get_latest_pending_for_session.assert_awaited_once()
    classifier.classify.assert_awaited_once()
