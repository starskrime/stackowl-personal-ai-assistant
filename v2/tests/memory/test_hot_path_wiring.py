"""Hot-path memory wiring: persistence in consolidate + recall in classify."""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import classify, consolidate
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.pipeline.turn_persist import persist_turn

pytestmark = pytest.mark.asyncio


def _make_state(
    *, session_id: str = "sess-1", input_text: str = "hello",
    responses: tuple = (),
) -> PipelineState:
    return PipelineState(
        trace_id=f"trace-{session_id}",
        session_id=session_id,
        input_text=input_text,
        channel="cli",
        owl_name="secretary",
        pipeline_step="start",
        responses=responses,
    )


async def test_consolidate_persists_user_and_assistant_text(tmp_db: DbPool) -> None:
    bridge = SqliteMemoryBridge(db=tmp_db)
    token = set_services(StepServices(memory_bridge=bridge))
    try:
        state = _make_state(
            input_text="My name is Bakir.",
            responses=(
                ResponseChunk(
                    content="Nice to meet you, Bakir!", is_final=True,
                    chunk_index=0, trace_id="t1", owl_name="secretary",
                ),
            ),
        )
        # F088: persistence relocated out of consolidate to the post-floor seam.
        out = await consolidate.run(state)
        await persist_turn(out)
    finally:
        reset_services(token)

    staged = await bridge.list_staged()
    assert any("Bakir" in s.content for s in staged)
    convo = [s for s in staged if s.source_type == "conversation"]
    assert len(convo) == 1
    assert "User: My name is Bakir." in convo[0].content
    assert "Assistant: Nice to meet you, Bakir!" in convo[0].content
    assert convo[0].source_ref == "sess-1"


async def test_consolidate_no_persist_when_both_empty(tmp_db: DbPool) -> None:
    bridge = SqliteMemoryBridge(db=tmp_db)
    token = set_services(StepServices(memory_bridge=bridge))
    try:
        state = _make_state(input_text="", responses=())
        out = await consolidate.run(state)
        await persist_turn(out)
    finally:
        reset_services(token)
    staged = await bridge.list_staged()
    assert len(staged) == 0


async def test_consolidate_persist_failure_does_not_raise(tmp_db: DbPool) -> None:
    """Bridge that raises on store must not propagate up."""

    class _BoomBridge(SqliteMemoryBridge):
        async def store(self, content: str, session_id: str, *, trust: object = None) -> None:
            raise RuntimeError("simulated DB outage")

    bridge = _BoomBridge(db=tmp_db)
    token = set_services(StepServices(memory_bridge=bridge))
    try:
        state = _make_state(
            input_text="hi",
            responses=(
                ResponseChunk(
                    content="hello", is_final=True, chunk_index=0,
                    trace_id="t", owl_name="secretary",
                ),
            ),
        )
        # Must not raise — persist_turn (F088 seam) is the best-effort store now.
        out = await consolidate.run(state)
        await persist_turn(out)
        assert out.responses == state.responses
    finally:
        reset_services(token)


async def test_classify_loads_recent_session_turns(tmp_db: DbPool) -> None:
    bridge = SqliteMemoryBridge(db=tmp_db)
    # Seed 3 prior turns into the same session.
    await bridge.store("User: turn 1\n\nAssistant: ok 1", "sess-Z")
    await bridge.store("User: turn 2\n\nAssistant: ok 2", "sess-Z")
    await bridge.store("User: turn 3\n\nAssistant: ok 3", "sess-Z")
    # A turn from a different session — must not appear.
    await bridge.store("User: other\n\nAssistant: other reply", "different-session")

    token = set_services(StepServices(memory_bridge=bridge))
    try:
        state = _make_state(session_id="sess-Z", input_text="follow-up")
        out = await classify.run(state)
    finally:
        reset_services(token)

    # Plan A (RC-C): recent turns are now loaded as REAL message turns into
    # state.history (oldest-first), not folded into memory_context as text.
    history_text = " ".join(m.content for m in out.history)
    assert "turn 1" in history_text
    assert "turn 2" in history_text
    assert "turn 3" in history_text
    assert "other" not in history_text  # session isolation
    # And they no longer leak into memory_context (avoids double-injection).
    assert "Recent conversation:" not in (out.memory_context or "")


async def test_classify_handles_bridge_None_gracefully() -> None:
    token = set_services(StepServices(memory_bridge=None))
    try:
        state = _make_state(session_id="sess-N", input_text="hi")
        out = await classify.run(state)
        assert out.memory_context is None or out.memory_context == ""
    finally:
        reset_services(token)


async def test_recent_conversation_turns_returns_oldest_first(tmp_db: DbPool) -> None:
    bridge = SqliteMemoryBridge(db=tmp_db)
    await bridge.store("first", "sess-O")
    await bridge.store("second", "sess-O")
    await bridge.store("third", "sess-O")
    turns = await bridge.recent_conversation_turns("sess-O", limit=10)
    contents = [t.content for t in turns]
    # Oldest-first chronological order
    assert contents == ["first", "second", "third"]


async def test_recent_conversation_turns_respects_limit(tmp_db: DbPool) -> None:
    bridge = SqliteMemoryBridge(db=tmp_db)
    for i in range(10):
        await bridge.store(f"turn-{i}", "sess-L")
    turns = await bridge.recent_conversation_turns("sess-L", limit=3)
    assert len(turns) == 3
    # Should be the LAST 3 stored, oldest-first
    assert [t.content for t in turns] == ["turn-7", "turn-8", "turn-9"]
