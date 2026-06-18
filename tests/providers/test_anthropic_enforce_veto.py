"""Unit tests for the structural-veto + escalation-reward nudge decision.

Proves the four behaviours of :func:`decide_nudge` (the pure helper that the
anthropic ``_enforce`` closure drives, reusable by the openai provider in T5):

  (A) the structural veto FIRES on a lying/erroring judge (``judge_directive``
      is None but the turn is structurally a give-up) -> persistence directive;
  (B) the escalation-reward cap: the budget decrements ONLY when a nudge
      produced no NEW tool call (a pure re-refusal); if the model escalated
      (``all_calls`` grew since the last nudge) the budget is NOT decremented;
  (C) an exhausted budget returns None (accept — the never-empty floor is the
      final backstop);
  (D) an explicit judge directive passes through and decrements.
"""
from __future__ import annotations

from stackowl.pipeline.persistence import PERSISTENCE_DIRECTIVE
from stackowl.pipeline.supervisor import decide_nudge

FAILED = [{"name": "browser_browse", "failed": True}]


def test_veto_fires_and_decrements_on_refusal() -> None:
    d, budget, last = decide_nudge(
        judge_directive=None,
        all_calls=FAILED,
        draft="...",
        nudge_budget=2,
        calls_at_last_nudge=None,
    )
    assert d == PERSISTENCE_DIRECTIVE
    assert budget == 1
    assert last == 1  # 1 call seen so far -> marker for the next round


def test_escalation_not_decremented() -> None:
    # After a prior nudge at 1 call, the model escalated (now 2 calls) -> reward,
    # budget unchanged.
    grown = [
        {"name": "browser_browse", "failed": True},
        {"name": "web_fetch", "failed": True},
    ]
    d, budget, last = decide_nudge(
        judge_directive=None,
        all_calls=grown,
        draft="...",
        nudge_budget=1,
        calls_at_last_nudge=1,
    )
    assert d == PERSISTENCE_DIRECTIVE
    assert budget == 1  # NOT decremented (escalated)
    assert last == 2  # marker advances to the new call count


def test_budget_exhausted_returns_none() -> None:
    d, budget, last = decide_nudge(
        judge_directive=None,
        all_calls=FAILED,
        draft="...",
        nudge_budget=0,
        calls_at_last_nudge=1,
    )
    assert d is None  # no budget -> accept (the floor is the backstop later)


def test_judge_directive_passthrough_decrements() -> None:
    d, budget, last = decide_nudge(
        judge_directive=PERSISTENCE_DIRECTIVE,
        all_calls=FAILED,
        draft="x",
        nudge_budget=2,
        calls_at_last_nudge=None,
    )
    assert d == PERSISTENCE_DIRECTIVE
    assert budget == 1


def test_no_giveup_returns_none() -> None:
    # A successful turn (no failed tools, substantive draft) is not a give-up;
    # the veto must NOT fire and the budget must be untouched.
    succeeded = [{"name": "shell", "failed": False}]
    d, budget, last = decide_nudge(
        judge_directive=None,
        all_calls=succeeded,
        draft="Here is the full result you asked for.",
        nudge_budget=2,
        calls_at_last_nudge=None,
    )
    assert d is None
    assert budget == 2  # untouched — no nudge issued
