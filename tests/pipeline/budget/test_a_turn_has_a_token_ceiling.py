"""A turn is bounded in TOKENS, because nothing else bounds what it spends.

MEASURED, 2026-08-29. Bakir: "I do not like ... also it did use a lot of tokens."
He is right, and there was no meter at all:

  * per-call there is a CHARACTER cap on one payload — not a turn total;
  * per-turn: nothing;  per-task: nothing;
  * the only cumulative meter is USD, and USD is **$0.00 for 123,527 of 123,528
    recorded calls**, because the model is unpriced. A cost cap on an unpriced
    model is a cap that can never fire.
  * the wall-clock cap is deliberately disabled for the default backstop.

So the only live bound was STEPS, and steps do not measure spend: trace f33c9fa0
billed 683,728 input tokens inside a 20-step budget, and `recover-task-925aa68-fix`
billed 3.9M across 137 rounds against the same 20-step ceiling.

WHY A TOKEN CAP IS SAFE TO ADD NOW AND WOULD NOT HAVE BEEN BEFORE. Until Stage 1a a
budget breach delivered nothing at all, so any cap risked converting a slow answer
into no answer. A breach now runs the toolless salvage and delivers what the turn
found, so the cap costs a partial answer rather than the whole turn.

WHERE THE NUMBER COMES FROM. Fleet-wide input tokens per trace: p50 11,574,
p90 106,752, p99 434,849; 84 traces over 500k and 24 over 1M. The ceiling sits
ABOVE p99 so ordinary work — including long research turns — never sees it, and
catches only the pathological tail.
"""

from __future__ import annotations

import pytest

from stackowl.authz.bounds import DEFAULT_TURN_MAX_INPUT_TOKENS, ResourceCaps
from stackowl.pipeline.budget.governor import BudgetGovernor


class _Clock:
    def monotonic(self) -> float:
        return 0.0


class _Tokens:
    def __init__(self, total: int) -> None:
        self.total = total

    def turn_cost_usd(self, trace_id: str) -> float:
        return 0.0

    def turn_input_tokens(self, trace_id: str) -> int:
        return self.total


def _gov(cap: int | None, spent: int) -> BudgetGovernor:
    return BudgetGovernor(
        ResourceCaps(max_steps=1000, max_input_tokens=cap),
        cost_tracker=_Tokens(spent), trace_id="t",
        started_monotonic=0.0, clock=_Clock(),
    )


def test_the_default_ceiling_sits_above_the_measured_p99() -> None:
    """A cap under p99 would fire on ordinary work; this one must not."""
    assert DEFAULT_TURN_MAX_INPUT_TOKENS > 434_849, (
        "the token ceiling is at or below the measured p99 — it will stop real work"
    )


def test_a_runaway_turn_is_STOPPED() -> None:
    """The f33c9fa0 shape: 683,728 tokens inside a 20-step budget."""
    breach = _gov(500_000, 683_728).check(iteration=5)
    assert breach is not None, "684k tokens did not breach any cap"
    assert breach.cap == "tokens", f"breached on {breach.cap!r}, not tokens"
    assert breach.actual == 683_728


def test_an_ORDINARY_turn_is_untouched() -> None:
    """p90 is 106,752. Nothing at that scale may notice this cap exists."""
    assert _gov(500_000, 106_752).check(iteration=5) is None


def test_NO_cap_means_no_token_check() -> None:
    """All-None caps must stay a no-op governor — the documented contract."""
    assert _gov(None, 10_000_000).check(iteration=5) is None


def test_a_missing_token_source_NEVER_disables_the_step_cap() -> None:
    """The module's stated rule: a missing cost signal never disables steps/time.

    A tracker with no token method must degrade to "no token bound", not to an
    AttributeError that takes the turn down with it.
    """

    class _NoTokens:
        def turn_cost_usd(self, trace_id: str) -> float:
            return 0.0

    gov = BudgetGovernor(
        ResourceCaps(max_steps=3, max_input_tokens=100),
        cost_tracker=_NoTokens(), trace_id="t",
        started_monotonic=0.0, clock=_Clock(),
    )
    assert gov.check(iteration=0) is None
    breach = gov.check(iteration=2)
    assert breach is not None and breach.cap == "steps"


def test_STEPS_still_win_over_tokens() -> None:
    """Order matters for the message the user gets; steps is the more specific."""
    gov = BudgetGovernor(
        ResourceCaps(max_steps=2, max_input_tokens=10),
        cost_tracker=_Tokens(999_999), trace_id="t",
        started_monotonic=0.0, clock=_Clock(),
    )
    breach = gov.check(iteration=1)
    assert breach is not None and breach.cap == "steps"


def test_the_cap_can_be_RAISED_like_every_other() -> None:
    """An interactive human must be able to say "keep going" here too."""
    gov = _gov(500_000, 683_728)
    assert gov.check(iteration=5) is not None
    gov.raise_caps("tokens")
    assert gov.check(iteration=5) is None, "raise_caps does not know about tokens"


@pytest.mark.asyncio
async def test_the_ledger_accumulates_tokens_alongside_cost() -> None:
    """One ledger, one eviction policy — not a second copy of the same rule."""
    from stackowl.providers.cost_tracker_helpers import TurnCostLedger

    led = TurnCostLedger()
    led.add("t", 0.0, input_tokens=19_423)
    led.add("t", 0.0, input_tokens=22_978)
    assert led.tokens("t") == 42_401
    assert led.total("t") == 0.0, "an unpriced model must still count tokens"
    assert led.tokens("never-seen") == 0
