"""Severity-aware consequential give-up veto — failing tests (Task 3, Step 1)."""
from stackowl.pipeline.supervisor import apply_structural_veto
from stackowl.pipeline.persistence import (
    CAPABILITY_GAP_DIRECTIVE, PERSISTENCE_DIRECTIVE, is_unachieved_consequential_giveup,
)


def test_signal():
    assert is_unachieved_consequential_giveup(cons_failures=1, cons_successes=0) is True
    assert is_unachieved_consequential_giveup(cons_failures=1, cons_successes=1) is False
    assert is_unachieved_consequential_giveup(cons_failures=0, cons_successes=0) is False


def test_veto_returns_capability_gap_when_consequential_unachieved():
    # The dressed-up case: trivial tool "succeeded" + substantive draft → the OLD
    # zombie signal does NOT fire; the NEW consequential signal must.
    d = apply_structural_veto(
        judge_directive=None,
        all_calls=[{"name": "write_file", "failed": False}],
        draft="I have built the full agentic bridge for you. Here are the steps...",
        cons_failures=1, cons_successes=0,
    )
    assert d == CAPABILITY_GAP_DIRECTIVE


def test_veto_silent_when_consequential_succeeded():
    d = apply_structural_veto(
        judge_directive=None, all_calls=[{"name": "send_email", "failed": False}],
        draft="Sent it.", cons_failures=1, cons_successes=1,
    )
    assert d is None


def test_explicit_judge_directive_still_wins():
    d = apply_structural_veto(
        judge_directive=PERSISTENCE_DIRECTIVE, all_calls=[], draft="x",
        cons_failures=1, cons_successes=0,
    )
    assert d == PERSISTENCE_DIRECTIVE
