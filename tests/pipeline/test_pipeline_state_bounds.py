"""E2-S2 — PipelineState carries creation_ceiling + task_envelope across evolve()."""

from __future__ import annotations

from stackowl.authz import BoundsSpec
from stackowl.pipeline.state import PipelineState


def _state(**kw: object) -> PipelineState:
    base = dict(
        trace_id="t", session_id="s", input_text="hi", channel="cli",
        owl_name="secretary", pipeline_step="",
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


def test_fields_default_none() -> None:
    s = _state()
    assert s.creation_ceiling is None
    assert s.task_envelope is None


def test_evolve_carries_creation_ceiling_by_identity() -> None:
    b = BoundsSpec(tools=frozenset({"x"}))
    s = _state().evolve(creation_ceiling=b)
    # identity (is) confirms evolve() is model_copy, not dump/reload
    assert s.creation_ceiling is b


def test_evolve_unrelated_field_preserves_envelope() -> None:
    b = BoundsSpec(tools=frozenset({"x"}))
    s = _state(creation_ceiling=b).evolve(input_text="changed")
    assert s.creation_ceiling is b
    assert s.input_text == "changed"
