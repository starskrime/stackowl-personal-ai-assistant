"""T3 / SP-2 — consolidate carries the merge/trust decision forward on state.

``consolidate.run`` computes ``merged_external`` where ``responses`` is still
empty (the only place the merge condition is true), and stamps it onto the
returned state via ``evolve`` so the post-floor ``persist_turn`` (F088/T4) can
read the trust decision WITHOUT recomputing it from post-floor ``responses``
(which would launder trust — LM-2/LM-9).
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState, ToolCall
from stackowl.pipeline.steps import consolidate
from stackowl.pipeline.streaming import ResponseChunk

pytestmark = pytest.mark.asyncio


def _state(*, responses: tuple = (), tool_calls: tuple = ()) -> PipelineState:
    return PipelineState(
        trace_id="t-me",
        session_id="sess-me",
        input_text="run a tool",
        channel="cli",
        owl_name="secretary",
        pipeline_step="consolidate",
        responses=responses,
        tool_calls=tool_calls,
    )


@pytest.mark.asyncio
async def test_merged_external_defaults_false() -> None:
    """The new immutable field defaults to False (byte-identical to clean turns)."""
    assert _state().merged_external is False


async def test_merge_branch_stamps_merged_external_true() -> None:
    token = set_services(StepServices())  # no bridge → persist is a no-op
    try:
        state = _state(
            tool_calls=(
                ToolCall(tool_name="shell", args={}, result="OUTPUT",
                         error=None, duration_ms=1.0),
            ),
        )
        out = await consolidate.run(state)
        assert out.merged_external is True
        # the merge produced the answer
        assert "OUTPUT" in "".join(c.content for c in out.responses)
    finally:
        reset_services(token)


async def test_clean_turn_leaves_merged_external_false() -> None:
    token = set_services(StepServices())
    try:
        state = _state(
            responses=(
                ResponseChunk(content="real answer", is_final=True, chunk_index=0,
                              trace_id="t-me", owl_name="secretary"),
            ),
        )
        out = await consolidate.run(state)
        assert out.merged_external is False
    finally:
        reset_services(token)


async def test_all_failed_merge_leaves_merged_external_false() -> None:
    """When the merge content is empty after the F095 filter, the carried flag is
    recomputed False from the FILTERED content (no trust laundering)."""
    token = set_services(StepServices())
    try:
        state = _state(
            tool_calls=(
                ToolCall(tool_name="shell", args={}, result="ERRBODY",
                         error="boom", duration_ms=1.0),
            ),
        )
        out = await consolidate.run(state)
        assert out.merged_external is False
        assert out.responses == ()
    finally:
        reset_services(token)
