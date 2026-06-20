"""P2 — live action recall: classify surfaces recent task_outcomes.

The agent must be able to answer "what did you just do?" — task_outcomes
captured each turn are surfaced into memory_context for the same session,
excluding the in-flight turn.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import classify

pytestmark = pytest.mark.asyncio


class _StubBridge:
    """Minimal MemoryBridge: no long-term facts, no history."""

    async def retrieve(self, query: str, session_id: str) -> str:  # noqa: ARG002
        return ""

    async def recent_conversation_turns(self, session_id: str, limit: int):  # noqa: ARG002
        return []


async def test_classify_surfaces_recent_actions_excluding_in_flight(tmp_db: DbPool) -> None:
    store = TaskOutcomeStore(db=tmp_db)
    # A prior completed turn in this session.
    await store.record(
        trace_id="prior-trace", session_id="sess-1", owl_name="secretary",
        channel="cli", success=True, latency_ms=10.0, tool_call_count=1,
        failure_class=None, step_durations={},
        input_text="deploy the staging server",
        response_text="Deployed staging successfully.",
        tool_sequence=("run_shell",),
    )
    # The in-flight turn — already captured under its own trace_id, must NOT echo.
    await store.record(
        trace_id="inflight-trace", session_id="sess-1", owl_name="secretary",
        channel="cli", success=True, latency_ms=1.0, tool_call_count=0,
        failure_class=None, step_durations={},
        input_text="what did you just do?",
        response_text="(pending)",
    )

    token = set_services(StepServices(db_pool=tmp_db, memory_bridge=_StubBridge()))
    try:
        state = PipelineState(
            trace_id="inflight-trace", session_id="sess-1",
            input_text="what did you just do?", channel="cli",
            owl_name="secretary", pipeline_step="classify",
            intent_class="standard", intent_classified=True,
        )
        out = await classify.run(state)
    finally:
        reset_services(token)

    ctx = out.memory_context or ""
    assert "What You Did Recently" in ctx
    assert "run_shell" in ctx
    assert "deploy the staging server" in ctx
    # In-flight turn excluded by exclude_trace_id.
    assert "what did you just do?" not in ctx
