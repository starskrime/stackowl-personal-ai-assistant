"""Task 2 — forward query embedding from classify step on PipelineState.

The classify step computes a semantic embedding of the user message once and
stashes it on PipelineState.query_embedding so the later assemble step can
score owned skills without re-embedding. Story B.
"""
from __future__ import annotations

from stackowl.pipeline.state import PipelineState


def _state(**kw: object) -> PipelineState:
    base: dict[str, object] = dict(
        trace_id="t",
        session_id="s",
        input_text="hello",
        channel="cli",
        owl_name="secretary",
        pipeline_step="classify",
    )
    base.update(kw)
    return PipelineState(**base)


def test_query_embedding_field_defaults_none() -> None:
    assert _state().query_embedding is None


def test_query_embedding_round_trips_via_evolve() -> None:
    s = _state().evolve(query_embedding=(0.1, 0.2, 0.3))
    assert s.query_embedding == (0.1, 0.2, 0.3)
