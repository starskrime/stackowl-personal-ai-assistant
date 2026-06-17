"""REACT-8 / F037 — tool-turn terminal signaling is consistent.

The audit flagged that every ResponseChunk in the tool path is is_final=False, so
no chunk is ever terminal. Investigation showed the streaming TERMINATOR is the
empty sentinel that ``StreamWriter.close()`` appends — and crucially
``StreamReader`` BREAKS on the first ``is_final=True`` chunk WITHOUT yielding it.
Therefore a CONTENT chunk must never carry is_final=True or its content is silently
SWALLOWED on delivery.

These tests pin the consistent contract:
  * a content chunk delivered through the real stream reader is NEVER lost;
  * ``consolidate``'s merged-tool-output chunk (a real tool-turn path) is delivered,
    not swallowed (the latent is_final=True bug this story fixes);
  * the close() sentinel is the terminal signal.
"""
from __future__ import annotations

import pytest

from stackowl.pipeline.state import PipelineState, ToolCall
from stackowl.pipeline.steps import consolidate
from stackowl.pipeline.streaming import ResponseChunk, StreamRegistry


async def _read_through_registry(responses: tuple[ResponseChunk, ...]) -> list[str]:
    """Write `responses` via a writer then close(); collect what the reader yields."""
    reg = StreamRegistry()
    writer, reader = reg.create("trace-final")
    for c in responses:
        await writer.write(c)
    await writer.close()
    out: list[str] = []
    async for chunk in reader:
        out.append(chunk.content)
    return out


@pytest.mark.asyncio
async def test_content_chunk_is_final_true_would_be_swallowed() -> None:
    """Contract guard: a content chunk marked is_final=True is dropped by the reader.

    This is WHY content chunks must stay is_final=False — proving the invariant the
    fix relies on, so a future regression to is_final=True is caught.
    """
    swallowed = await _read_through_registry(
        (ResponseChunk(content="LOST", is_final=True, chunk_index=0,
                       trace_id="trace-final", owl_name="o"),)
    )
    assert swallowed == [], "reader is expected to break on an is_final content chunk"

    delivered = await _read_through_registry(
        (ResponseChunk(content="KEPT", is_final=False, chunk_index=0,
                       trace_id="trace-final", owl_name="o"),)
    )
    assert delivered == ["KEPT"]


@pytest.mark.asyncio
async def test_consolidate_merged_chunk_is_not_swallowed() -> None:
    """consolidate merges successful tool output when responses is empty; that
    merged chunk must be DELIVERABLE (is_final=False), not swallowed by the reader."""
    state = PipelineState(
        trace_id="trace-final", session_id="s1", input_text="hi", channel="cli",
        owl_name="o", pipeline_step="consolidate",
        tool_calls=(ToolCall(tool_name="t", args={}, result="TOOL OUTPUT", error=None, duration_ms=1.0),),
    )
    out_state = await consolidate.run(state)

    assert out_state.responses, "consolidate did not merge tool output"
    merged = out_state.responses[-1]
    assert merged.content == "TOOL OUTPUT"
    assert merged.is_final is False, (
        "the merged content chunk must be is_final=False or the StreamReader swallows it"
    )
    # End-to-end: the merged content survives delivery through the real reader.
    delivered = await _read_through_registry(out_state.responses)
    assert delivered == ["TOOL OUTPUT"]
