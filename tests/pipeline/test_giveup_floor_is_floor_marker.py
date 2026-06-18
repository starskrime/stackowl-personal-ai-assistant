"""T1 / SP-1 — the consequential-giveup floor chunk must carry is_floor=True.

Floor-origin must be detectable downstream so:
  * F088 persist skips the floor prose as a promotable fact,
  * the critical-failure cascade does NOT treat the giveup floor as a genuine
    answer (which would suppress the localized apology),
  * the pipeline floor band recognizes a provider floor as replaceable (no double floor).
"""

from __future__ import annotations

import pytest

from stackowl.infra import tool_outcome_ledger as tol
from stackowl.pipeline.critical_failure import _has_usable_response
from stackowl.pipeline.giveup_floor import surface_consequential_giveup_floor
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _state(text: str) -> PipelineState:
    return PipelineState(
        trace_id="t",
        session_id="s",
        input_text="send the email",
        channel="cli",
        owl_name="secretary",
        pipeline_step="deliver",
        responses=(
            ResponseChunk(
                content=text, is_final=False, chunk_index=0,
                trace_id="t", owl_name="secretary",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_giveup_floor_chunk_is_marked_is_floor() -> None:
    token = tol.bind()
    try:
        tol.record_tool_outcome(
            name="send_email", action_severity="consequential", success=False,
        )
        s = _state("I have built the full agentic bridge for you. Here are the steps...")
        out = await surface_consequential_giveup_floor(s)
        # The replaced chunk(s) must ALL carry the floor marker.
        assert out.responses
        assert all(c.is_floor for c in out.responses)
    finally:
        tol.reset(token)


@pytest.mark.asyncio
async def test_giveup_floor_is_not_a_usable_genuine_answer() -> None:
    """A response made up only of the giveup floor must not look like a real answer
    to the critical-failure cascade (else the apology is wrongly suppressed)."""
    token = tol.bind()
    try:
        tol.record_tool_outcome(
            name="send_email", action_severity="consequential", success=False,
        )
        s = _state("Done — sent the email!")
        out = await surface_consequential_giveup_floor(s)
        assert not _has_usable_response(out)
    finally:
        tol.reset(token)
