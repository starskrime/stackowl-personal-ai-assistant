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
