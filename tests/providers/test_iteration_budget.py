"""D02.2 — the consumable iteration budget and its refunds.

The budget replaced a bare `range(resolved_iterations)`, which was already a
correct BOUND. What it could not express is that not every pass through the loop
is work: three sites `continue` after CORRECTING the model rather than advancing
the answer. Measured over four days of production logs — 10-16 leak/loop-guard
events and 18-42 give-up directives PER DAY, against a 20-iteration interactive
budget.
"""

from __future__ import annotations

import pytest

from stackowl.providers.iteration_budget import IterationBudget


def test_it_bounds_the_loop():
    b = IterationBudget(3)
    assert [b.consume() for _ in range(4)] == [True, True, True, False]


def test_a_refund_buys_exactly_one_more_round():
    b = IterationBudget(2)
    b.consume(); b.consume()
    assert not b.consume(), "budget should be spent"
    b.refund("format_fix")
    assert b.consume(), "a refunded round must be usable"
    assert not b.consume(), "and only one"


def test_a_refund_can_never_exceed_the_cap():
    """The one thing the budget exists to prevent. A refund without a matching
    consume would let the loop run past max_total."""
    b = IterationBudget(2)
    for _ in range(5):
        b.refund("bogus")
    assert b.used == 0
    assert [b.consume() for _ in range(3)] == [True, True, False]


def test_refunds_are_counted_separately_from_use():
    b = IterationBudget(5)
    b.consume(); b.consume()
    b.refund("give_up_directive")
    assert b.used == 1
    assert b.refunded == 1
    assert b.remaining == 4


def test_a_zero_budget_never_runs():
    assert IterationBudget(0).consume() is False


def test_a_negative_cap_is_clamped():
    b = IterationBudget(-5)
    assert b.max_total == 0 and b.consume() is False


@pytest.mark.parametrize(
    "provider", ["anthropic_provider", "openai_provider"]
)
def test_BOTH_providers_refund_all_three_corrective_paths(provider):
    """The refunds are the point; a budget object without them is just a slower
    range(). Asserted on both providers because the loops are twins and the
    openai one is what this deployment actually runs."""
    import pathlib

    src = pathlib.Path(f"src/stackowl/providers/{provider}.py").read_text()
    for reason in ("steer_folded", "format_fix", "give_up_directive"):
        assert f'iter_budget.refund("{reason}")' in src, (
            f"{provider} does not refund {reason}"
        )


@pytest.mark.parametrize("provider", ["anthropic_provider", "openai_provider"])
def test_the_iteration_budget_does_not_shadow_the_TOKEN_budget(provider):
    """`budget` was already the token budget passed to trim_messages_to_budget in
    this scope. Naming the new object `budget` silently fed an IterationBudget
    into the trimmer — caught by mypy, not by any test, so it is pinned here."""
    import pathlib

    src = pathlib.Path(f"src/stackowl/providers/{provider}.py").read_text()
    assert "iter_budget = IterationBudget(" in src
    assert "\n        budget = IterationBudget(" not in src
    assert "trim_messages_to_budget(messages, budget)" in src


# --------------------------------------------------------------------------- #
# THE HANG. Refunds made the loop unbounded, and only an integration test caught
# it: test_enforce_exit_safety folds a steer at the give-up boundary on EVERY
# round, so `used` never advanced and consume() returned True forever. That is
# an infinite loop in production, not a slow test.
# --------------------------------------------------------------------------- #


def test_refunding_EVERY_round_still_terminates():
    """The exact shape that hung: a corrective path firing on every iteration."""
    b = IterationBudget(3)
    rounds = 0
    while b.consume():
        rounds += 1
        b.refund("steer_folded")
        assert rounds <= 10, "loop did not terminate — refunds are unbounded again"
    assert rounds == 6, f"expected 2x the cap as the worst case, got {rounds}"


def test_refunds_are_capped_at_the_budget_size():
    """Once a turn has been corrected as many times as it had iterations, it is
    not converging. Further refunds are declined so the budget drains and the
    graceful max-out can produce an answer."""
    b = IterationBudget(2)
    for _ in range(10):
        if b.consume():
            b.refund("give_up_directive")
    assert b.refunded == 2, f"refunds must cap at max_total, got {b.refunded}"


def test_the_worst_case_is_exactly_twice_the_cap():
    """Stated as a property because it is the guarantee that makes refunds safe:
    a bounded loop stays bounded."""
    for cap in (1, 5, 20, 45):
        b = IterationBudget(cap)
        rounds = 0
        while b.consume():
            rounds += 1
            b.refund("format_fix")
            assert rounds <= cap * 2 + 1, f"cap={cap} ran away at {rounds}"
        assert rounds == cap * 2
