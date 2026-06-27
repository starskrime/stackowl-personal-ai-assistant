"""ADR-7 step 2 — the ADR-1/2/3 authorities emit a typed Decision to the turn ledger.

Verifies the ADR §Verification spec: a turn that ACCEPTED an effect, RECOVERED a failure,
ACTED on an assumption, was STEERED by a learned heuristic, and CLASSIFIED an intent
produces a ledger with all of those Decisions and their reasons. Also pins the flag-OFF
contract: with no ledger bound, the same calls emit nothing (byte-identical)."""

from __future__ import annotations

import pytest

from stackowl.infra import decision_ledger as dl
from stackowl.infra.decision_ledger import Decision, render_why
from stackowl.interaction.intent_classifier import ClarifyIntentClassifier
from stackowl.interaction.reversibility_resolver import (
    Decision as RevDecision,
)
from stackowl.interaction.reversibility_resolver import (
    Reversibility,
    ReversibilityResolver,
)
from stackowl.pipeline import lesson_context as lc
from stackowl.pipeline.acceptance_authority import AcceptanceAuthority, NonEmptyText
from stackowl.pipeline.recovery_actuator import Failure, RecoveryActuator


class _NoProviderRegistry:
    """Stub registry: the classifier fail-safes when no fast provider resolves. The
    empty-message router path short-circuits before this is even consulted."""

    def get_by_tier(self, _tier: str) -> None:
        return None


async def _exercise_all_three() -> None:
    AcceptanceAuthority().observe(NonEmptyText(), success=True, output="real output")
    await RecoveryActuator().recover(
        Failure(name="flaky_tool", transient=True),
        attempt=_ok_thunk,
        record=False,
    )
    ReversibilityResolver().resolve(
        RevDecision(reversibility=Reversibility.reversible(via="undo"), choices=("only",)),
    )


async def _ok_thunk() -> object:
    return "recovered-value"


@pytest.mark.asyncio
async def test_three_authorities_each_emit_a_decision_with_reason():
    token = dl.bind()
    try:
        await _exercise_all_three()
        decisions = dl.get_decisions()
        by_point = {d.point: d for d in decisions}
        assert {"acceptance", "recovery", "reversibility"} <= by_point.keys()
        assert by_point["acceptance"].verdict == "accepted"
        assert by_point["recovery"].verdict == "recovered"
        assert by_point["recovery"].alternatives_considered == ("retry",)
        assert by_point["reversibility"].verdict == "act"
        # every consequential Decision carries a non-empty reason
        for point in ("acceptance", "recovery", "reversibility"):
            assert by_point[point].reason
    finally:
        dl.reset(token)


@pytest.mark.asyncio
async def test_no_opinion_acceptance_emits_nothing():
    token = dl.bind()
    try:
        # NonEmptyText with empty output is a REFUTED opinion → still emits; a NULL
        # post-condition has no opinion → must NOT pollute the ledger.
        AcceptanceAuthority().observe(None, success=True, output="")
        assert dl.get_decisions() == ()
    finally:
        dl.reset(token)


@pytest.mark.asyncio
async def test_flag_off_unbound_is_byte_identical():
    # No bind() (flag off) → the authorities behave exactly as before, ledger empty.
    await _exercise_all_three()
    assert dl.get_decisions() == ()


@pytest.mark.asyncio
async def test_full_turn_emits_all_five_authority_decisions():
    """ADR §Verification: recovery + reversibility + acceptance + learned_context +
    router each land in one turn's ledger, each with a non-empty reason."""
    token = dl.bind()
    lesson_token = lc.bind()
    try:
        lc.set_surfaced(
            (lc.SurfacedLesson(lesson_id="L1", source_type="x",
                               content="keep answers short", similarity=0.9),),
        )
        await _exercise_all_three()
        lc.record_applied("L1", "kept the answer short")
        # router: the empty-message path fail-safes to "answer" (low confidence) without
        # needing a provider — enough to exercise the public-boundary emit.
        await ClarifyIntentClassifier(_NoProviderRegistry()).explain_answer(
            question="which file?", choices=(), message="",
        )

        by_point = {d.point: d for d in dl.get_decisions()}
        assert {
            "acceptance", "recovery", "reversibility", "learned_context", "router",
        } <= by_point.keys()
        assert by_point["learned_context"].verdict == "L1"
        assert by_point["router"].verdict == "answer"
        assert by_point["router"].evidence.get("confidence") == "low"
        for point in (
            "acceptance", "recovery", "reversibility", "learned_context", "router",
        ):
            assert by_point[point].reason
    finally:
        lc.reset(lesson_token)
        dl.reset(token)


def test_render_why_non_empty_for_populated_ledger():
    decisions = (
        Decision(point="router", verdict="answer", reason="clear_verdict"),
        Decision(point="recovery", verdict="recovered", reason="retry succeeded"),
        Decision(point="reversibility", verdict="act"),  # no reason → still rendered
    )
    rendered = render_why(decisions)
    assert "router — answer — clear_verdict" in rendered
    assert "recovery — recovered — retry succeeded" in rendered
    assert "reversibility — act" in rendered  # reason omitted, line still present
    assert rendered.count("\n") == 2  # one line per decision


def test_render_why_empty_for_empty_ledger():
    assert render_why(()) == ""
