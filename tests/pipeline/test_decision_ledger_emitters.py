"""ADR-7 step 2 — the ADR-1/2/3 authorities emit a typed Decision to the turn ledger.

Verifies the ADR §Verification spec (partial — learned_context/router emitters land in a
later slice): a turn that ACCEPTED an effect, RECOVERED a failure, and ACTED on an
assumption produces a ledger with all three Decisions and their reasons. Also pins the
flag-OFF contract: with no ledger bound, the same calls emit nothing (byte-identical)."""

from __future__ import annotations

import pytest

from stackowl.infra import decision_ledger as dl
from stackowl.interaction.reversibility_resolver import (
    Decision as RevDecision,
    Reversibility,
    ReversibilityResolver,
)
from stackowl.pipeline.acceptance_authority import AcceptanceAuthority, NonEmptyText
from stackowl.pipeline.recovery_actuator import Failure, RecoveryActuator


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
