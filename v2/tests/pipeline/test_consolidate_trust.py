"""Task 7: consolidate stamps tool-merged turns as untrusted.

When the merge branch fires (state.tool_calls present AND state.responses empty),
the persisted conversation fact must carry trust="untrusted" because the assistant
text comes from external/untrusted tool output, not the LLM itself.

A clean turn (no merge) must continue to be stamped trust="self".

Mirrors the env from tests/memory/test_hot_path_wiring.py:
  - tmp_db  fixture from tests/conftest.py
  - set_services / reset_services to inject StepServices with a SqliteMemoryBridge
  - consolidate.run(state) as the entry point
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState, ToolCall
from stackowl.pipeline.steps import consolidate
from stackowl.pipeline.streaming import ResponseChunk

pytestmark = pytest.mark.asyncio


def _base_state(*, session_id: str = "sess-trust", input_text: str = "run a tool") -> PipelineState:
    return PipelineState(
        trace_id=f"trace-{session_id}",
        session_id=session_id,
        input_text=input_text,
        channel="cli",
        owl_name="secretary",
        pipeline_step="start",
    )


async def test_tool_merged_turn_staged_untrusted(tmp_db: DbPool) -> None:
    """Merge branch fires (tool_calls set, responses empty) → trust must be 'untrusted'."""
    bridge = SqliteMemoryBridge(db=tmp_db)
    token = set_services(StepServices(memory_bridge=bridge))
    try:
        # Construct state that triggers the merge branch:
        #   state.tool_calls is non-empty AND state.responses is empty ()
        state = _base_state().evolve(
            tool_calls=(
                ToolCall(
                    tool_name="shell",
                    args={"command": "ls"},
                    result="file1.txt\nfile2.txt",
                    error=None,
                    duration_ms=12.0,
                ),
            ),
            responses=(),
        )
        await consolidate.run(state)
    finally:
        reset_services(token)

    rows = await tmp_db.fetch_all(
        "SELECT trust FROM staged_facts WHERE source_type='conversation' ORDER BY staged_at DESC LIMIT 1"
    )
    assert rows, "Expected a staged conversation fact to be persisted"
    assert rows[0]["trust"] == "untrusted", (
        f"Tool-merged turn must be stamped 'untrusted', got {rows[0]['trust']!r}"
    )


async def test_clean_turn_staged_self(tmp_db: DbPool) -> None:
    """Clean turn (responses set, no tool-merge) → trust must be 'self'."""
    bridge = SqliteMemoryBridge(db=tmp_db)
    token = set_services(StepServices(memory_bridge=bridge))
    try:
        # Construct state that does NOT trigger the merge branch:
        #   responses already populated (the normal LLM-generated-answer path)
        state = _base_state(input_text="hello").evolve(
            responses=(
                ResponseChunk(
                    content="Here is your answer.",
                    is_final=True,
                    chunk_index=0,
                    trace_id="trace-sess-trust",
                    owl_name="secretary",
                ),
            ),
            tool_calls=(),
        )
        await consolidate.run(state)
    finally:
        reset_services(token)

    rows = await tmp_db.fetch_all(
        "SELECT trust FROM staged_facts WHERE source_type='conversation' ORDER BY staged_at DESC LIMIT 1"
    )
    assert rows, "Expected a staged conversation fact to be persisted"
    assert rows[0]["trust"] == "self", (
        f"Clean turn must be stamped 'self', got {rows[0]['trust']!r}"
    )
