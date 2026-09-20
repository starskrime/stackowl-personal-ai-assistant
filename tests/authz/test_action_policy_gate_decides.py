"""Story 4.4 -- the action-policy gate's decision table (`authz.action_policy.
decide`) and its attendance primitive (`attends`).

Modelled on the spec's own I/O & Edge-Case Matrix, verbatim: every row is one
test, named after the scenario it proves.
"""

from __future__ import annotations

import pytest

from stackowl.authz.action_policy import ActionPolicyDecision, attends, decide


def test_owner_reversible_write_runs_at_once() -> None:
    result = decide(severity="write", reversible=True, requester_kind="owner")
    assert result == ActionPolicyDecision(outcome="run_at_once", attending=True)


def test_owner_irreversible_write_needs_step_up() -> None:
    result = decide(severity="write", reversible=False, requester_kind="owner")
    assert result.outcome == "needs_step_up"


def test_owner_consequential_reversible_needs_step_up() -> None:
    """Severity outranks reversibility -- CONSEQUENTIAL always needs step-up,
    even for a reversible command."""
    result = decide(severity="consequential", reversible=True, requester_kind="owner")
    assert result.outcome == "needs_step_up"


def test_owl_reversible_write_needs_approval_never_run_at_once() -> None:
    """An owl/crew request always gets a deterministic read-back (FR35)."""
    result = decide(severity="write", reversible=True, requester_kind="owl")
    assert result.outcome == "needs_approval"


@pytest.mark.parametrize(
    "severity,reversible",
    [("write", False), ("consequential", True), ("consequential", False)],
)
def test_owl_irreversible_or_consequential_needs_step_up(
    severity: str, reversible: bool,
) -> None:
    result = decide(severity=severity, reversible=reversible, requester_kind="owl")
    assert result.outcome == "needs_step_up"


def test_voice_unverified_reversible_write_runs_at_once() -> None:
    result = decide(
        severity="write", reversible=True, requester_kind="voice-unverified",
    )
    assert result.outcome == "run_at_once"


def test_voice_unverified_consequential_needs_step_up() -> None:
    """A later spoken 'yes' cannot resolve it (FR41)."""
    result = decide(
        severity="consequential", reversible=True, requester_kind="voice-unverified",
    )
    assert result.outcome == "needs_step_up"


def test_autonomous_irreversible_needs_step_up() -> None:
    """No standing authority exists yet to bypass it (FR32)."""
    result = decide(severity="write", reversible=False, requester_kind="autonomous")
    assert result.outcome == "needs_step_up"


def test_autonomous_reversible_write_runs_at_once() -> None:
    """Attendance is declared, not load-bearing, this story — an autonomous
    reversible WRITE decides the same as the owner's own (Design Notes)."""
    result = decide(severity="write", reversible=True, requester_kind="autonomous")
    assert result.outcome == "run_at_once"


def test_attends_autonomous_is_false() -> None:
    assert attends("autonomous") is False


@pytest.mark.parametrize("requester_kind", ["owner", "owl", "voice-unverified"])
def test_attends_every_other_kind_is_true(requester_kind: str) -> None:
    assert attends(requester_kind) is True  # type: ignore[arg-type]


def test_decide_carries_attending_alongside_the_outcome() -> None:
    owner_decision = decide(severity="write", reversible=True, requester_kind="owner")
    assert owner_decision.attending is True
    autonomous_decision = decide(
        severity="write", reversible=False, requester_kind="autonomous",
    )
    assert autonomous_decision.attending is False


def test_an_undeclared_severity_is_refused() -> None:
    with pytest.raises(ValueError, match="severity"):
        decide(severity="delete-everything", reversible=True, requester_kind="owner")


def test_the_decision_is_frozen() -> None:
    result = decide(severity="write", reversible=True, requester_kind="owner")
    with pytest.raises(Exception):  # noqa: B017 — dataclasses.FrozenInstanceError
        result.outcome = "needs_step_up"  # type: ignore[misc]
