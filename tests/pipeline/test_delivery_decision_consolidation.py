"""PA0 characterization — the consolidated DeliveryDecision must AGREE, per turn
state, with every give-up predicate it replaces. Proves consolidation, no drift.

For a matrix of turn states we assert:
  * decide_delivery(state).consequential_giveup == is_consequential_giveup_now(state)
  * decide_delivery(state).unrecovered_failures == _unrecovered_consequential_failures(state)
  * decide_delivery(state).failed_capability == the floor's old inline failed_name
and that the stamped/computed tally-level predicates (is_unachieved_consequential_giveup,
is_effectful_failure) line up with the verdict — so no site can disagree.
"""

from __future__ import annotations

import pytest

from stackowl.infra import tool_outcome_ledger
from stackowl.pipeline.delivery_gate import (
    _name_failed_capability,
    _unrecovered_consequential_failures,
    decide_delivery,
    is_consequential_giveup_now,
)
from stackowl.pipeline.persistence import is_unachieved_consequential_giveup
from stackowl.pipeline.state import PipelineState


def _state(**kw: object) -> PipelineState:
    base: dict[str, object] = dict(
        trace_id="t",
        session_id="s",
        input_text="do the thing",
        channel="cli",
        owl_name="owl",
        pipeline_step="execute",
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


# (id, snapshot kwargs, expected give-up). All carry consequential_snapshot_taken=True
# so the snapshot path is exercised exactly as execute stamps it.
_MATRIX = [
    ("no_giveup_no_activity", dict(consequential_snapshot_taken=True), False),
    (
        "consequential_giveup",
        dict(consequential_failures=("send_email",), consequential_snapshot_taken=True),
        True,
    ),
    (
        "effectful_failure_recovered",
        dict(
            consequential_failures=("send_email",),
            recovered_consequential=("send_email",),
            consequential_snapshot_taken=True,
        ),
        False,
    ),
    (
        "failure_with_other_success",
        dict(
            consequential_failures=("send_email",),
            consequential_successes=("post_message",),
            consequential_snapshot_taken=True,
        ),
        False,
    ),
    (
        "clean_success",
        dict(consequential_successes=("send_email",), consequential_snapshot_taken=True),
        False,
    ),
    (
        "budget_capped_unverified_giveup",
        dict(
            consequential_failures=("send_email",),
            consequential_successes=("write_file",),  # incidental local write only
            delivered_successes=(),
            budget_capped=True,
            consequential_snapshot_taken=True,
        ),
        True,
    ),
]


@pytest.mark.parametrize("name,kw,expected_giveup", _MATRIX, ids=[m[0] for m in _MATRIX])
def test_decision_agrees_with_old_predicates(
    name: str, kw: dict[str, object], expected_giveup: bool
) -> None:
    state = _state(**kw)
    decision = decide_delivery(state)

    # 1. verdict == is_consequential_giveup_now (the predicate every floor/persist site read)
    assert decision.consequential_giveup == is_consequential_giveup_now(state)
    assert decision.consequential_giveup is expected_giveup

    if decision.consequential_giveup:
        # On give-up: named capability == the floor's old inline failed_name, computed
        # off the same set _unrecovered_consequential_failures yields.
        assert decision.unrecovered_failures == frozenset(
            _unrecovered_consequential_failures(state)
        )
        assert decision.failed_capability == _name_failed_capability(
            state, decision.unrecovered_failures
        )
    else:
        # Early-out: a non-give-up turn never touches the unrecovered set (byte-identical
        # to the floor's pre-PA0 early return — no extra live-ledger read on a clean turn).
        assert decision.unrecovered_failures == frozenset()
        assert decision.failed_capability is None


@pytest.mark.parametrize("name,kw,expected_giveup", _MATRIX, ids=[m[0] for m in _MATRIX])
def test_tally_predicate_consistent_with_verdict(
    name: str, kw: dict[str, object], expected_giveup: bool
) -> None:
    """The tally-level predicate (is_unachieved_consequential_giveup) and the bridged
    guard together reproduce the verdict — so a tally-only reader cannot disagree."""
    state = _state(**kw)
    cf = len(state.consequential_failures)
    cs = (
        len(state.delivered_successes)
        if state.budget_capped
        else len(state.consequential_successes)
    )
    unachieved = is_unachieved_consequential_giveup(cons_failures=cf, cons_successes=cs)
    has_unrecovered = bool(_unrecovered_consequential_failures(state))
    assert (unachieved and has_unrecovered) == decide_delivery(state).consequential_giveup


def test_verdict_recomputes_off_final_state_not_memoized() -> None:
    """decide_delivery is the single source of truth at READ time: the verdict reflects
    the FINAL state, so flipping budget_capped (which switches the success tally to the
    delivered-only subset) flips the verdict — proving no stale memoization. This is the
    P0 budget-cap incident guard at unit level."""
    # send_email failed, only an incidental local write succeeded, nothing delivered.
    base = _state(
        consequential_failures=("send_email",),
        consequential_successes=("write_file",),
        delivered_successes=(),
        consequential_snapshot_taken=True,
    )
    # Not capped: write_file counts as a consequential success → not a give-up.
    assert decide_delivery(base).consequential_giveup is False
    # Capped: delivered-only tally is empty → honest-floor give-up.
    assert decide_delivery(base.evolve(budget_capped=True)).consequential_giveup is True


def test_decision_is_deterministic_value_equal_across_calls() -> None:
    """Two calls on the same state yield equal decisions (pure function of state)."""
    state = _state(consequential_failures=("send_email",), consequential_snapshot_taken=True)
    assert decide_delivery(state) == decide_delivery(state)


def test_live_ledger_fallback_matches_predicate_no_snapshot() -> None:
    """No snapshot on state ⇒ decide_delivery falls back to the live ledger, exactly
    like is_consequential_giveup_now(state) does — covers the ledger failed_name branch."""
    token = tool_outcome_ledger.bind()
    try:
        tool_outcome_ledger.record_tool_outcome(
            name="send_email", action_severity="consequential", success=False,
        )
        state = _state()  # no snapshot fields → has_consequential_snapshot is False
        decision = decide_delivery(state)
        assert decision.consequential_giveup is True
        assert decision.consequential_giveup == is_consequential_giveup_now(state)
        assert decision.failed_capability == "send_email"
        assert decision.unrecovered_failures == frozenset(
            _unrecovered_consequential_failures(state)
        )
    finally:
        tool_outcome_ledger.reset(token)
