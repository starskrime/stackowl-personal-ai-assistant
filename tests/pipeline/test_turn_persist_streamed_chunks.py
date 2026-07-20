"""Streamed-chunk join regression — persist_turn must ""-join, never "\\n"-join.

Confirmed production incident (2026-07-19, telegram): a streamed turn stores
one ResponseChunk per token, so a "\\n".join persisted
"word\\nword\\nword" into memory/history; recalled into later turns' context,
the model mimicked the one-word-per-line format in LIVE replies for several
turns. retry_actuator.py:145 already documented the rule ("".join — one chunk
per token); turn_persist/backends.shared/shadow_validator were the stragglers.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.pipeline.turn_persist import persist_turn


def _chunk(text: str, i: int) -> ResponseChunk:
    return ResponseChunk(
        content=text, is_final=False, chunk_index=i,
        trace_id="trace-s", owl_name="secretary",
    )


@pytest.mark.asyncio
async def test_streamed_token_chunks_persist_as_flowing_text(monkeypatch) -> None:
    stored: dict = {}

    class FakeBridge:
        async def store(self, content, session_id, *, trust=None):
            stored["content"] = content
            stored["session_id"] = session_id

    class FakeServices:
        memory_bridge = FakeBridge()
        retry_queue_store = None

    monkeypatch.setattr(
        "stackowl.pipeline.turn_persist.get_services", lambda: FakeServices()
    )

    # Streamed shape: one chunk per token, whitespace carried inside the chunks.
    state = PipelineState(
        trace_id="trace-s", session_id="sess-s", input_text="hello",
        channel="telegram", owl_name="secretary", pipeline_step="respond",
        responses=tuple(
            _chunk(t, i) for i, t in enumerate(["The ", "quick ", "brown ", "fox."])
        ),
    )

    await persist_turn(state)

    assert "content" in stored, "turn was not persisted at all"
    assert "The quick brown fox." in stored["content"]
    assert "The\nquick" not in stored["content"], (
        "newline-joined token chunks — this is the exact memory poison that "
        "made the live model mimic one-word-per-line output"
    )
