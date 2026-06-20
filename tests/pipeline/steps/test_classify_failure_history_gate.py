"""Tests for _should_surface_failure_history admission gate in classify step.

Story #2 — Failure-history is never injected on an unclassified/non-work turn.

The gate is True ONLY when the router positively classified the turn as
``standard`` (intent_classified=True AND intent_class="standard"). Every other
combination — direct-address default, conversational, clarify — returns False.
"""

from __future__ import annotations

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.classify import _should_surface_failure_history


def _s(**kw: object) -> PipelineState:
    base: dict[str, object] = dict(
        trace_id="t", session_id="s", input_text="x",
        owl_name="secretary", channel="cli", pipeline_step="start",
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


def test_surfaces_on_classified_standard_turn() -> None:
    """A positively-classified standard turn should surface failure history."""
    assert _should_surface_failure_history(
        _s(intent_class="standard", intent_classified=True)
    ) is True


def test_suppressed_on_direct_address_default_standard() -> None:
    """The bug: standard-by-default + never classified must NOT surface failures."""
    assert _should_surface_failure_history(
        _s(intent_class="standard", intent_classified=False)
    ) is False


def test_suppressed_on_conversational() -> None:
    """Conversational turns never surface failure history even if classified."""
    assert _should_surface_failure_history(
        _s(intent_class="conversational", intent_classified=True)
    ) is False


def test_suppressed_on_clarify() -> None:
    """Clarify turns never surface failure history."""
    assert _should_surface_failure_history(
        _s(intent_class="clarify", intent_classified=True)
    ) is False
