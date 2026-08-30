"""Every 5 steps, check whether the plan actually moved — and replan if it did not.

BAKIR'S ASK, 2026-08-29, verbatim: "we can have like check of progress for every 5
steps. If agent did not move on task then optimize and replan actions again, it is
like evaulation pipeline on every 5 steps, which will check progress of current
plan and actions if no progrrss then go to beginning do planning and reprocess
again. Also in today flow i do not see the plan steps which will plan actions and
evaulate."

THIS IS THAT PIPELINE. Two things were missing and both are now in place: the plan
is per-TURN (it was a process global, so a check would have read another chat's
plan), and the plan tools are always PRESENTED (they were dropped on 869 of 869
capped turns by an alphabetical tail-break, which is why `todo` has 4 invocations
in the platform's whole history).

WHAT IS TAKEN FROM HIS DESIGN, AND WHAT THE EVIDENCE CHANGED.

  TAKEN: the interval of 5, the "did it move?" question, and re-planning when it
  did not. All three are his and all three are here.

  CHANGED: WHO answers "did it move?". He proposed the model evaluate its own
  progress. Measured, an LLM judging progress from its own trajectory scores
  0.54-0.65 AUROC — near chance — and degrades as the trace gets longer, i.e. it
  is least reliable exactly when it is most needed. A structural detector scores
  0.83-0.95. So the check reads the PLAN'S OWN ITEM STATUSES, which the model
  already maintains: it is free, deterministic, and cannot hallucinate progress.
  A judge-inclusive arm also measured +129% tokens for identical quality, which is
  the opposite of what he asked for.

  So: his interval, his question, his replan — answered by counting, not by asking.

THE TWO STUCK SHAPES, and the second is the one that matters most here:

  1. A plan exists and NOTHING has advanced for 5 steps -> revise the plan,
     carrying forward what has been ruled out (never "go to the beginning" from
     zero: Reflexion's gain comes from the buffer being carried forward, and
     loss-of-history is itself a catalogued failure mode).
  2. NO plan exists after 5 steps -> the turn is long enough to need one. This is
     what makes planning HAPPEN on the turns that need it, while a short turn is
     never nudged and never charged for the feature.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.budget.callback import make_budget_callback
from stackowl.providers.react_callback import ReActIterationState


class _Governor:
    def check(self, iteration: int, *, tool_calls: int | None = None):  # noqa: ANN201
        return None

    def steps_remaining(self, iteration: int, *, tool_calls: int | None = None):  # noqa: ANN201
        return 100  # far from the cap, so the converge directive never fires here

    def raise_caps(self, cap: str) -> None: ...


class _Plan:
    """Stands in for the live PlanStore's counts()."""

    def __init__(self) -> None:
        self.state = {"pending": 0, "in_progress": 0, "completed": 0,
                      "cancelled": 0, "total": 0}

    def __call__(self) -> dict[str, int]:
        return dict(self.state)

    def set(self, **kw: int) -> None:
        self.state.update(kw)
        self.state["total"] = (
            self.state["pending"] + self.state["in_progress"]
            + self.state["completed"] + self.state["cancelled"]
        )


def _cb(plan: _Plan):  # noqa: ANN202
    return make_budget_callback(
        _Governor(), interactive=False, clarify=None, session_key="s",
        channel="cli", plan_counts=plan,
    )


def _st(i: int) -> ReActIterationState:
    return ReActIterationState(iteration=i, messages=[], tool_call_records=[])


async def _run(gate, plan: _Plan, steps: int) -> list[str]:  # noqa: ANN001
    folded = []
    for i in range(steps):
        got = await gate(_st(i))
        if got:
            folded.append(got[0]["content"])
    return folded


@pytest.mark.asyncio
async def test_a_SHORT_turn_is_never_nudged() -> None:
    """Most turns finish in a few steps. They must not pay for this at all."""
    plan = _Plan()
    assert await _run(_cb(plan), plan, 4) == []


@pytest.mark.asyncio
async def test_a_long_turn_with_NO_plan_is_told_to_make_one() -> None:
    """This is what makes planning HAPPEN — on exactly the turns that need it."""
    plan = _Plan()
    folded = await _run(_cb(plan), plan, 6)
    assert folded, "5 steps with no plan and the model was never asked for one"
    assert "plan" in folded[0].lower()


@pytest.mark.asyncio
async def test_a_plan_that_is_ADVANCING_is_left_alone() -> None:
    """The guard must be narrow: progress means silence, however long the turn."""
    plan = _Plan()
    plan.set(pending=3, in_progress=1)
    gate = _cb(plan)
    folded = []
    for i in range(12):
        # one item completes every other step — real, visible progress
        if i % 2 == 0:
            plan.set(pending=max(0, plan.state["pending"] - 1),
                     completed=plan.state["completed"] + 1)
        got = await gate(_st(i))
        if got:
            folded.append(got[0]["content"])
    assert folded == [], f"an advancing turn was interrupted: {folded}"


@pytest.mark.asyncio
async def test_a_STUCK_plan_triggers_a_replan() -> None:
    """The defect Bakir reported: burning steps with nothing moving."""
    plan = _Plan()
    plan.set(pending=3, in_progress=1)
    folded = await _run(_cb(plan), plan, 8)
    assert folded, "the plan never moved for 8 steps and nothing intervened"
    text = folded[0].lower()
    assert "plan" in text
    assert "ruled out" in text or "different" in text or "revise" in text


@pytest.mark.asyncio
async def test_the_replan_carries_history_FORWARD_never_from_zero() -> None:
    """"Go to the beginning" is the one part of the proposal the evidence rejects.

    Reflexion's gain comes from carrying a reflection buffer FORWARD; discarding
    history is itself a catalogued failure mode, and restart-style strategies get
    worse with more budget, not better.
    """
    plan = _Plan()
    plan.set(pending=2, in_progress=1)
    folded = await _run(_cb(plan), plan, 8)
    text = folded[0].lower()
    # Asserting the phrase is ABSENT was wrong — the directive says "do NOT start
    # over", which is the instruction we want. Assert the negation instead, which
    # is what actually distinguishes carry-forward from restart.
    assert "do not start over" in text or "do not restart" in text, (
        f"the replan does not forbid restarting from zero: {text}"
    )
    assert "keep everything you have already established" in text, (
        "the directive does not tell it to carry its findings forward"
    )
    assert "ruled out" in text


@pytest.mark.asyncio
async def test_it_does_not_fire_every_step_once_stuck() -> None:
    """A directive repeated every step is context growth on the worst turns."""
    plan = _Plan()
    plan.set(pending=3)
    folded = await _run(_cb(plan), plan, 20)
    assert len(folded) <= 3, (
        f"the replan directive fired {len(folded)} times in 20 steps"
    )


@pytest.mark.asyncio
async def test_progress_RESETS_the_window() -> None:
    """Four quiet steps then a completion must not count toward the next five."""
    plan = _Plan()
    plan.set(pending=3, in_progress=1)
    gate = _cb(plan)
    folded = []
    for i in range(9):
        if i == 3:
            plan.set(pending=2, completed=1)  # progress on step 4
        got = await gate(_st(i))
        if got:
            folded.append(i)
    assert folded and folded[0] >= 7, (
        f"the no-progress window did not reset on real progress: fired at {folded}"
    )


@pytest.mark.asyncio
async def test_NO_plan_source_wired_is_a_clean_no_op() -> None:
    """Every existing caller passes no plan source and must be unaffected."""
    gate = make_budget_callback(
        _Governor(), interactive=False, clarify=None, session_key="s", channel="cli",
    )
    for i in range(20):
        assert await gate(_st(i)) is None


@pytest.mark.asyncio
async def test_a_BROKEN_plan_source_never_costs_the_turn() -> None:
    """Advisory state must never raise into the loop it is advising."""
    def _boom() -> dict[str, int]:
        raise RuntimeError("plan store is gone")

    gate = make_budget_callback(
        _Governor(), interactive=False, clarify=None, session_key="s",
        channel="cli", plan_counts=_boom,
    )
    for i in range(8):
        assert await gate(_st(i)) is None
