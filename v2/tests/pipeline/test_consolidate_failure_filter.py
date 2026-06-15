"""T2 / F095 — consolidate must NOT merge a FAILED tool's output as the answer.

The raw-merge path joined every truthy ``tc.result`` with no failure awareness,
so a failed tool's non-empty error body was delivered as the answer. The fix
filters on ``tc.error is None`` (the only typed success signal at the pipeline
layer — there is NO ``failed`` bool on ToolCall, and the TOOL_FAILED_MARKER is
stripped before results reach pipeline state).
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.state import PipelineState, ToolCall
from stackowl.pipeline.steps import consolidate

pytestmark = pytest.mark.asyncio


def _state(*tool_calls: ToolCall) -> PipelineState:
    return PipelineState(
        trace_id="t-merge",
        session_id="sess-merge",
        input_text="run a tool",
        channel="cli",
        owl_name="secretary",
        pipeline_step="consolidate",
        responses=(),
        tool_calls=tool_calls,
    )


async def test_failed_tool_body_never_merged_as_answer() -> None:
    """A single failed tool (error set, non-empty result) must produce NO merged
    chunk, and its error body must never appear in any response."""
    state = _state(
        ToolCall(
            tool_name="send_email",
            args={"to": "x@y.z"},
            result="SMTP 535 auth failed: secret-token-leak",
            error="auth failure",
            duration_ms=5.0,
        ),
    )
    out = await consolidate.run(state)
    assert out.responses == (), "a failed tool must not be merged into the answer"
    combined = "".join(c.content for c in out.responses)
    assert "SMTP 535" not in combined
    assert "secret-token-leak" not in combined


async def test_mixed_only_successful_tool_output_merged() -> None:
    """When some tools failed and some succeeded, only the successful bodies merge."""
    state = _state(
        ToolCall(
            tool_name="read_file", args={}, result="GOOD CONTENT",
            error=None, duration_ms=1.0,
        ),
        ToolCall(
            tool_name="shell", args={}, result="FAILBODY rm: permission denied",
            error="nonzero exit", duration_ms=1.0,
        ),
    )
    out = await consolidate.run(state)
    combined = "".join(c.content for c in out.responses)
    assert "GOOD CONTENT" in combined
    assert "FAILBODY" not in combined


async def test_all_failed_tools_no_merge_floor_owns_turn() -> None:
    """When every tool failed, no chunk is emitted — the floor band owns the turn."""
    state = _state(
        ToolCall(
            tool_name="shell", args={}, result="error body 1",
            error="boom", duration_ms=1.0,
        ),
    )
    out = await consolidate.run(state)
    assert out.responses == ()
