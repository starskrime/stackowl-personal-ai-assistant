"""A single round may not eat the budget the whole turn has to live on.

OPERATOR DECISION, ESC-147 (2026-09-05). He OVERRODE his own 2026-07-22 ruling — "Raised
(owner decision) … both are now pure backstops against a genuinely pathological single
tool result, not shaping ceilings" — with the measurement in front of him. That reversal
is why this was escalated instead of changed quietly, and why the answer had to be his.

THE UNITS-AND-SCOPE MISMATCH. `trim_messages_to_budget` asks "does this round fit the
model's WINDOW?" in CHARS, per ROUND, derived from `context_chars` (1,000,000 chars
fallback ~= 260,000 tokens). `BudgetGovernor` asks "has this TURN spent its budget?" in
TOKENS, cumulatively (500,000). Neither knows the other exists, so the per-round bound
sits ~4x above what the per-turn bound can survive even ONCE.

MEASURED, trace `goal-f6b00937`: 21 calls, 1,643,689 input tokens, rounds growing
7,011 -> 11,028 -> 49,135 -> 87,608 -> **162,912**. That largest round is 32.6% of the
entire turn budget and ~627,000 chars — comfortably under the 1,000,000-char trimmer,
which never fired once across all 21 rounds.

WHY A SHARE OF WHAT REMAINS, AND NOT A FLAT NUMBER. A flat ceiling either binds on
ordinary turns (p50 is 2 prefix-carrying rounds; taxing them buys nothing) or never binds
at all. A share of the REMAINING budget degrades gracefully instead: early in a turn it is
barely below today's value, so nothing ordinary changes; late in a turn it tightens, so a
turn spends its last budget on several rounds rather than one. Half means a turn can
always afford at least one more round after this one.

IT CAN ONLY EVER TIGHTEN. The result is `min(configured, share-of-remaining)`, so a
deployment with a small `context_chars` is unaffected, and no path can widen a window the
model cannot accept.
"""

from __future__ import annotations

import pytest

from stackowl.providers._truncate import CONTEXT_CHAR_BUDGET, round_char_budget

CPT = 3.85  # measured chars/token for this deployment, not the folklore 4.0


def test_no_budget_information_changes_nothing() -> None:
    """The safety property. A caller that supplies no governor — every test double,
    every path not yet threaded — must get byte-identical behaviour to today."""
    assert round_char_budget(CONTEXT_CHAR_BUDGET, None) == CONTEXT_CHAR_BUDGET
    assert round_char_budget(500_000, None) == 500_000


def test_it_can_only_tighten_never_widen() -> None:
    """A deployment whose model accepts 200k chars must never be handed more because
    the turn happens to have budget left."""
    assert round_char_budget(200_000, 500_000) == 200_000


def test_early_in_a_turn_it_barely_binds() -> None:
    """p50 is 2 prefix-carrying rounds. Ordinary turns must not pay for the tail's
    problem: at a full 500,000-token budget the ceiling is ~962,500 chars, just under
    today's 1,000,000 fallback."""
    early = round_char_budget(CONTEXT_CHAR_BUDGET, 500_000)
    assert 900_000 < early <= CONTEXT_CHAR_BUDGET


def test_late_in_a_turn_it_actually_binds() -> None:
    """With 100,000 tokens left, one round may spend ~50,000 — so the turn gets
    several more rounds instead of one enormous one."""
    late = round_char_budget(CONTEXT_CHAR_BUDGET, 100_000)
    assert late == int(100_000 * CPT * 0.5)
    assert late / CPT == pytest.approx(50_000, rel=0.01)


def test_the_measured_runaway_round_would_have_been_trimmed() -> None:
    """The case this exists for. `goal-f6b00937`'s 162,912-token round (~627,211
    chars) sailed under the flat 1,000,000-char ceiling. With ~150,000 tokens left of
    the turn, it no longer does."""
    budget = round_char_budget(CONTEXT_CHAR_BUDGET, 150_000)
    runaway_chars = int(162_912 * CPT)

    assert runaway_chars > budget, (
        f"the {runaway_chars:,}-char round still fits a {budget:,}-char ceiling"
    )


def test_a_turn_can_always_afford_another_round() -> None:
    """Half of what remains, by construction — so trimming never strands a turn with
    a budget it cannot use. This is the property that makes the share safe to apply
    late rather than a way of strangling the final rounds."""
    for remaining in (400_000, 100_000, 20_000, 5_000):
        allowed_tokens = round_char_budget(CONTEXT_CHAR_BUDGET, remaining) / CPT
        assert allowed_tokens <= remaining / 2 + 1


def test_an_exhausted_budget_does_not_produce_a_zero_length_request() -> None:
    """At or below zero the governor breaches on its next check anyway, so trimming
    to nothing would gut the request for no benefit — and a zero-char budget is the
    kind of pathological input that turns a bounded turn into a broken one."""
    assert round_char_budget(CONTEXT_CHAR_BUDGET, 0) == CONTEXT_CHAR_BUDGET
    assert round_char_budget(CONTEXT_CHAR_BUDGET, -5) == CONTEXT_CHAR_BUDGET


@pytest.mark.tripwire
def test_both_providers_ask_for_the_remaining_budget() -> None:
    """ONE RULE, NOT TWO. Both provider loops trim before each call; a fix applied to
    one is the "actuator wired on only some paths" shape this repo names as its most
    common defect. Structural because no unit test can prove the OTHER provider was
    threaded too."""
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "src" / "stackowl" / "providers"
    for name in ("openai_provider.py", "anthropic_provider.py"):
        text = (src / name).read_text(encoding="utf-8")
        assert "round_char_budget(" in text, (
            f"{name} still trims against the window alone — one round there can "
            "consume budget the whole turn needs"
        )
