"""ResponseChunk.actions — tappable follow-up actions carried from a
slash-command CommandResponse (see startup/orchestrator.py::_deliver_command_stub)."""

from __future__ import annotations


def test_response_chunk_actions_defaults_empty() -> None:
    from stackowl.pipeline.streaming import ResponseChunk

    chunk = ResponseChunk(content="hi", is_final=True, chunk_index=0, trace_id="t1", owl_name="system")
    assert chunk.actions == ()


def test_response_chunk_carries_actions() -> None:
    from stackowl.commands.response import Action
    from stackowl.pipeline.streaming import ResponseChunk

    chunk = ResponseChunk(
        content="pick one", is_final=True, chunk_index=0, trace_id="t1", owl_name="system",
        actions=(Action(label="Go", command="/help"),),
    )
    assert len(chunk.actions) == 1
