"""Unit tests for surface_overclaim_gate (Task 6 — Turn Progress Supervisor).

Covers the structural overclaim delivery-gate: a confident non-floor draft that
delivered nothing while a tool failed/bounced must be replaced with the honest
floor.  Runs AFTER surface_consequential_giveup_floor in both backends.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.overclaim_gate import surface_overclaim_gate
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk  # noqa: F401

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(**kw: object) -> PipelineState:
    base: dict[str, object] = dict(
        trace_id="t-gate",
        session_id="s",
        input_text="send my report",
        channel="cli",
        owl_name="o",
        pipeline_step="execute",
        # default: turn made progress, no failures — cleared
        turn_made_progress=True,
        no_progress_tools=(),
        consequential_failures=(),
        consequential_snapshot_taken=False,
        delivered_successes=(),
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


def _draft(content: str, *, is_floor: bool = False) -> ResponseChunk:
    return ResponseChunk(
        content=content,
        is_final=False,
        chunk_index=0,
        trace_id="t-gate",
        owl_name="o",
        is_floor=is_floor,
    )


# ---------------------------------------------------------------------------
# Blocked cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overclaim_blocked() -> None:
    """Consequential failure + nothing delivered + non-floor draft → gate blocks."""
    state = _state(
        responses=(_draft("I've successfully sent your report!"),),
        consequential_failures=("send_image",),
        consequential_snapshot_taken=True,
        delivered_successes=(),
    )
    result = await surface_overclaim_gate(state)
    assert result.overclaim_blocked is True
    assert len(result.responses) == 1
    chunk = result.responses[0]
    assert chunk.is_floor is True
    # Overclaim text must be gone.
    assert "successfully" not in chunk.content.lower()


@pytest.mark.asyncio
async def test_no_progress_overclaim_blocked() -> None:
    """no_progress_tools + nothing delivered + non-floor draft → gate blocks."""
    state = _state(
        responses=(_draft("Task completed! Your code is running."),),
        turn_made_progress=False,
        no_progress_tools=("execute_code",),
        delivered_successes=(),
    )
    result = await surface_overclaim_gate(state)
    assert result.overclaim_blocked is True
    chunk = result.responses[0]
    assert chunk.is_floor is True


# ---------------------------------------------------------------------------
# Not-blocked cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delivered_success_not_blocked() -> None:
    """delivered_successes non-empty → legitimate delivery, NOT blocked."""
    state = _state(
        responses=(_draft("Your report has been sent!"),),
        consequential_failures=("send_image",),
        consequential_snapshot_taken=True,
        delivered_successes=("send_image",),
    )
    result = await surface_overclaim_gate(state)
    assert result.overclaim_blocked is False
    assert result.responses[0].is_floor is False
    assert "Your report has been sent!" in result.responses[0].content


@pytest.mark.asyncio
async def test_conversational_zero_tool_not_blocked() -> None:
    """Pure conversational turn (no failures, no no_progress_tools) → CLEARED."""
    state = _state(
        responses=(_draft("Sure, I can help with that!"),),
        # defaults: consequential_failures=(), no_progress_tools=(), delivered_successes=()
    )
    result = await surface_overclaim_gate(state)
    assert result.overclaim_blocked is False
    assert result.responses[0].content == "Sure, I can help with that!"


@pytest.mark.asyncio
async def test_already_floor_not_double_processed() -> None:
    """Already-honest floor draft → returned untouched (no double floor)."""
    floor_chunk = _draft("I wasn't able to complete that.", is_floor=True)
    state = _state(
        responses=(floor_chunk,),
        consequential_failures=("send_image",),
        consequential_snapshot_taken=True,
        delivered_successes=(),
    )
    result = await surface_overclaim_gate(state)
    assert result.overclaim_blocked is False
    assert result.responses[0] is floor_chunk


@pytest.mark.asyncio
async def test_empty_response_not_blocked() -> None:
    """Empty/whitespace-only response + consequential failure → CLEARED (no overclaim)."""
    state = _state(
        responses=(_draft("   "),),  # whitespace only
        consequential_failures=("send_image",),
        consequential_snapshot_taken=True,
        delivered_successes=(),
    )
    result = await surface_overclaim_gate(state)
    # Empty response guard clears it before the failure check
    assert result.overclaim_blocked is False
    assert result.responses[0].content == "   "
