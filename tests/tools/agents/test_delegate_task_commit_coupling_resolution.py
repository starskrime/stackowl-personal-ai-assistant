"""commit_coupling resolution replaces the honest-terminal gate for durable parents (D1 §6.2).

DELIBERATE behavior change vs Story D: a durable, write-capable child that
NEVER STARTED is now a DEFINITE safe-retry (not honest_uncertain); a durable
child whose only effects are transactional+committed is DEFINITE done. An
unconfirmed effect in-flight stays honest_uncertain. Non-durable parents keep
Story D's _can_side_effect honest-terminal behavior verbatim.
"""

from __future__ import annotations

import itertools

import pytest


# The resolution is a pure decision over (child_started, has_uncertain_effect,
# has_uncommitted_intent, child_terminal) — test the helper directly so the
# table is exhaustively covered.
def test_never_started_is_definite_safe_retry() -> None:
    from stackowl.tools.agents.delegate_task import resolve_commit_coupling_answer

    answer = resolve_commit_coupling_answer(
        child_started=False, has_uncertain_effect=False, has_uncommitted_intent=False,
        child_terminal=False,
    )
    assert answer == "safe_retry"


def test_terminal_all_transactional_is_definite_done() -> None:
    from stackowl.tools.agents.delegate_task import resolve_commit_coupling_answer

    answer = resolve_commit_coupling_answer(
        child_started=True, has_uncertain_effect=False, has_uncommitted_intent=False,
        child_terminal=True,
    )
    assert answer == "done"


def test_unconfirmed_in_flight_stays_honest_uncertain() -> None:
    from stackowl.tools.agents.delegate_task import resolve_commit_coupling_answer

    answer = resolve_commit_coupling_answer(
        child_started=True, has_uncertain_effect=True, has_uncommitted_intent=False,
        child_terminal=False,
    )
    assert answer == "honest_uncertain"


def test_uncommitted_intent_stays_honest_uncertain() -> None:
    from stackowl.tools.agents.delegate_task import resolve_commit_coupling_answer

    answer = resolve_commit_coupling_answer(
        child_started=True, has_uncertain_effect=False, has_uncommitted_intent=True,
        child_terminal=True,
    )
    assert answer == "honest_uncertain"


def _expected(
    child_started: bool,
    has_uncertain_effect: bool,
    has_uncommitted_intent: bool,
    child_terminal: bool,
) -> str:
    """Independent oracle for the §6.2 honesty table (computed, not the impl).

    Precedence: never-started ⇒ safe_retry (pure profit, no intent rows ever
    written so nothing could have acted); else any unresolved uncertainty
    (unconfirmed effect lacking witnessed commit, OR a non-transactional intent
    not yet committed) ⇒ honest_uncertain; else terminal-and-clean ⇒ done; else
    still in flight with nothing resolvable ⇒ honest_uncertain.
    """
    if not child_started:
        return "safe_retry"
    if has_uncertain_effect or has_uncommitted_intent:
        return "honest_uncertain"
    if child_terminal:
        return "done"
    return "honest_uncertain"


@pytest.mark.parametrize(
    ("child_started", "has_uncertain_effect", "has_uncommitted_intent", "child_terminal"),
    list(itertools.product([False, True], repeat=4)),
)
def test_decision_table_exhaustive(
    child_started: bool,
    has_uncertain_effect: bool,
    has_uncommitted_intent: bool,
    child_terminal: bool,
) -> None:
    """All 16 combinations of the 4 bools resolve exactly per §6.2."""
    from stackowl.tools.agents.delegate_task import resolve_commit_coupling_answer

    answer = resolve_commit_coupling_answer(
        child_started=child_started,
        has_uncertain_effect=has_uncertain_effect,
        has_uncommitted_intent=has_uncommitted_intent,
        child_terminal=child_terminal,
    )
    assert answer == _expected(
        child_started, has_uncertain_effect, has_uncommitted_intent, child_terminal,
    )
