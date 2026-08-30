"""The model must learn its step budget is nearly gone, while it can still act.

BAKIR'S REQUEST, 2026-08-29: *"we can have like check of progress for every 5 steps.
If agent did not move on task then optimize and replan actions again."*

The measured literature contradicts the MECHANISM (an LLM judging its own progress
from a trajectory scores 0.54-0.65 AUROC — near chance — and a fixed interval showed
the highest backtracking of any planning frequency), but confirms the PROBLEM: agents
are not natively budget-aware, and they DO change strategy when told the budget.

So the trigger is a BUDGET FRACTION, not a step interval, and the message is a
directive rather than a question. It costs no model call at all: it rides the
existing ``on_iteration_complete`` fold contract, which providers already splice into
``messages`` before the next round.

WHY IT MUST FIRE ONCE. Re-sending the directive every round after 75% would grow the
transcript on exactly the turns already closest to the cap — paying the accumulating
context cost to repeat something the model has already read.

TRACE f33c9fa0 is the case: 16 rounds, 20-step cap, and nothing ever told it to
converge until the cap simply killed the turn.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.budget.callback import make_budget_callback
from stackowl.providers.react_callback import ReActIterationState


class _Governor:
    """Steps-only governor stub with the real check()/remaining arithmetic."""

    def __init__(self, max_steps: int) -> None:
        self.max_steps = max_steps

    def check(self, iteration: int, *, tool_calls: int | None = None):  # noqa: ANN201
        from stackowl.exceptions import BudgetBreach
        done = iteration + 1 if tool_calls is None else max(iteration + 1, tool_calls)
        if done >= self.max_steps:
            return BudgetBreach("steps", float(self.max_steps), float(done))
        return None

    def steps_remaining(self, iteration: int, *, tool_calls: int | None = None):  # noqa: ANN201
        done = iteration + 1 if tool_calls is None else max(iteration + 1, tool_calls)
        return max(0, self.max_steps - done)

    def raise_caps(self, cap: str) -> None:  # pragma: no cover - unused here
        pass


def _cb(max_steps: int = 20):  # noqa: ANN201
    return make_budget_callback(
        _Governor(max_steps), interactive=False, clarify=None,
        session_key="s", channel="cli",
    )


def _st(i: int) -> ReActIterationState:
    return ReActIterationState(iteration=i, messages=[], tool_call_records=[])


@pytest.mark.asyncio
async def test_nothing_is_folded_early_in_the_turn() -> None:
    """A directive at round 2 of 20 is noise that costs context every round after."""
    gate = _cb(20)
    for i in range(0, 10):
        assert await gate(_st(i)) is None, f"folded a directive at round {i + 1}/20"


@pytest.mark.asyncio
async def test_the_directive_arrives_at_the_budget_FRACTION() -> None:
    """75% consumed — late enough to be true, early enough to still act on."""
    gate = _cb(20)
    folded = None
    for i in range(0, 18):
        got = await gate(_st(i))
        if got:
            folded = (i + 1, got)
            break
    assert folded, "the model was never told its budget was running out"
    step, msgs = folded
    assert 14 <= step <= 16, f"fired at step {step}/20 — not the 75% mark"
    assert isinstance(msgs, list) and msgs
    assert msgs[0]["role"] == "user"


@pytest.mark.asyncio
async def test_the_directive_says_HOW_MANY_steps_are_left() -> None:
    """"Converge" without a number is advice; with a number it is a budget."""
    gate = _cb(20)
    text = ""
    for i in range(0, 18):
        got = await gate(_st(i))
        if got:
            text = got[0]["content"]
            break
    assert text
    assert any(ch.isdigit() for ch in text), f"no step count in the directive: {text}"
    low = text.lower()
    assert "step" in low
    assert "report" in low or "converge" in low or "wrap" in low


@pytest.mark.asyncio
async def test_it_fires_at_most_ONCE_per_turn() -> None:
    """Repeating it every round grows context on the turns nearest the cap."""
    gate = _cb(20)
    fired = 0
    for i in range(0, 18):
        if await gate(_st(i)):
            fired += 1
    assert fired == 1, f"the directive was folded {fired} times in one turn"


@pytest.mark.asyncio
async def test_a_turn_with_NO_step_cap_gets_no_directive() -> None:
    """No budget means no number to report — a directive would be a lie."""

    class _Uncapped:
        def check(self, iteration, *, tool_calls=None):  # noqa: ANN001,ANN201
            return None

        def steps_remaining(self, iteration, *, tool_calls=None):  # noqa: ANN001,ANN201
            return None

        def raise_caps(self, cap): ...  # noqa: ANN001

    gate = make_budget_callback(
        _Uncapped(), interactive=False, clarify=None, session_key="s", channel="cli",
    )
    for i in range(0, 40):
        assert await gate(_st(i)) is None


@pytest.mark.asyncio
async def test_the_breach_still_wins_over_the_directive() -> None:
    """At the cap the turn must STOP, not receive advice."""
    from stackowl.exceptions import BudgetBreach

    gate = _cb(20)
    for i in range(0, 18):
        await gate(_st(i))
    with pytest.raises(BudgetBreach):
        await gate(_st(19))
