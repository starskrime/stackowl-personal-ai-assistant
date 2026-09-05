"""One round may consume a third of a turn's token budget, and nothing says so.

MEASURED 2026-09-05, trace `goal-f6b00937`: 21 calls, 1,643,689 input tokens, with
per-round inputs growing 7,011 -> 11,028 -> 49,135 -> 87,608 -> **162,912**. That
largest single round is 32.6% of the entire 500,000-token TURN budget — and the
per-round trimmer never fired once, because its budget is 1,000,000 CHARS
(`providers/_truncate.py:28`) and 162,912 tokens is only ~627,000 chars.

THE ROOT CAUSE IS A UNITS-AND-SCOPE MISMATCH BETWEEN TWO LIVE BOUNDS.
`trim_messages_to_budget` asks "does this round fit the model's WINDOW?" in CHARS,
per ROUND, derived from `context_chars`. `BudgetGovernor` asks "has this TURN spent
its budget?" in TOKENS, cumulatively. Neither knows the other exists, so the
per-round bound sits roughly 4x above what the per-turn bound can survive even
ONCE. Four rounds of that shape end the turn; the trimmer considers all four
unremarkable.

WHY A DETECTOR AND NOT A LOWER LIMIT. Lowering the trimmer's budget would reverse a
recorded owner decision (`providers/_truncate.py:22-26`, 2026-07-22: "no artificial
80% shrink … pure backstops, not shaping ceilings"), which is escalated rather than
overridden here. But the reason nobody noticed the mismatch for months is separate
and fixable: **nothing measures a round against the turn's budget.** `grep -rn
input_tokens src/stackowl/health/` returns ZERO — the only signal a turn is
consuming abnormally is the cap itself firing, which is the brake, not a gauge.
That is the platform's own guide star ("if this degrades SILENTLY, what notices?")
going unanswered on its most expensive axis.

ONE SOURCE, NOT TWO. `tokens_remaining()` reuses the exact arithmetic `check()`
uses — prior durable attempts plus this attempt's meter — for the reason
`steps_remaining`'s own docstring already gives: "the number the model is told
cannot disagree with the number that will actually stop it — two copies of one rule
is how this repo's defects usually start."
"""

from __future__ import annotations

from stackowl.pipeline.budget.governor import BudgetGovernor


class _Tokens:
    """Stub cost tracker — the shape the governor duck-types."""

    def __init__(self, tokens: int) -> None:
        self._tokens = tokens

    def turn_input_tokens(self, trace_id: str) -> int:  # noqa: ARG002
        return self._tokens

    def turn_cost_usd(self, trace_id: str) -> float:  # noqa: ARG002
        return 0.0


class _Clock:
    def monotonic(self) -> float:
        return 0.0


def _governor(*, max_tokens: int | None, used: int, prior: int = 0) -> BudgetGovernor:
    from stackowl.authz.bounds import ResourceCaps

    return BudgetGovernor(
        ResourceCaps(max_steps=1000, max_input_tokens=max_tokens),
        cost_tracker=_Tokens(used), trace_id="t",
        started_monotonic=0.0, clock=_Clock(), prior_input_tokens=prior,
    )


def test_no_cap_means_no_number_rather_than_a_guess() -> None:
    """None, not 0 and not infinity. A turn with no token cap has no remaining
    budget to report, and inventing one would make the alarm below fire on turns
    that are not bounded at all."""
    assert _governor(max_tokens=None, used=10_000).tokens_remaining() is None


def test_it_reports_what_is_left() -> None:
    g = _governor(max_tokens=500_000, used=120_000)
    assert g.tokens_remaining() == 380_000


def test_it_counts_prior_durable_attempts_like_the_cap_does() -> None:
    """The measured failure this protects: `recover-cf91d7c8c8d0` breached three
    times (514,503 / 549,238 / 584,905) because each durable attempt is SEEDED
    with its predecessor's spend while the step count resets. A remaining-budget
    reader that ignored the seed would tell the model it had room the cap was
    about to refuse."""
    g = _governor(max_tokens=500_000, used=40_000, prior=470_000)
    assert g.tokens_remaining() == 0


def test_remaining_never_goes_negative() -> None:
    assert _governor(max_tokens=500_000, used=584_905).tokens_remaining() == 0


def test_the_gauge_AGREES_with_the_brake() -> None:
    """The property that matters, and the one a second copy would break.

    At exactly the cap, `check()` must breach and `tokens_remaining()` must read
    zero. If these two ever disagree, the model is told it has budget that the
    governor is simultaneously refusing — which is precisely the "two copies of
    one rule" failure `steps_remaining` was written to avoid.
    """
    for used in (0, 250_000, 499_999, 500_000, 700_000):
        g = _governor(max_tokens=500_000, used=used)
        remaining = g.tokens_remaining()
        breached = g.check(iteration=0) is not None and g.check(iteration=0).cap == "tokens"
        assert (remaining == 0) == breached, (
            f"at used={used:,} the gauge says {remaining} left while the brake "
            f"says breached={breached} — the two disagree"
        )


def test_an_unreadable_meter_does_not_invent_a_number() -> None:
    """`check()` already treats an unreadable meter as 0 used and logs a warning
    rather than disabling steps/time. The gauge must fail the same way, not raise
    into a caller that only wanted to log a line."""

    class _Broken:
        def turn_input_tokens(self, trace_id: str) -> int:  # noqa: ARG002
            raise RuntimeError("meter is down")

        def turn_cost_usd(self, trace_id: str) -> float:  # noqa: ARG002
            return 0.0

    from stackowl.authz.bounds import ResourceCaps

    g = BudgetGovernor(
        ResourceCaps(max_steps=1000, max_input_tokens=500_000),
        cost_tracker=_Broken(), trace_id="t",
        started_monotonic=0.0, clock=_Clock(),
    )
    assert g.tokens_remaining() == 500_000  # nothing measured spent, cap intact


# --- the detector itself -------------------------------------------------------


def test_an_outsized_round_is_reported_with_its_share() -> None:
    """The measured shape: a round that eats a third of what is left."""
    from stackowl.pipeline.budget.callback import round_is_outsized

    share = round_is_outsized(before=500_000, after=337_088)  # the goal-f6b00937 round
    assert share is not None
    assert 0.32 < share < 0.33


def test_an_ordinary_round_says_NOTHING() -> None:
    """A line on every round is a line nobody reads. The measured ordinary delta
    is ~150-170 tokens against a 500,000 budget — 0.03%."""
    from stackowl.pipeline.budget.callback import round_is_outsized

    assert round_is_outsized(before=500_000, after=499_830) is None


def test_a_stalled_or_RAISED_budget_is_not_an_alarm() -> None:
    """`raise_caps()` doubles the ceiling mid-turn, so `after` can legitimately
    EXCEED `before`. Reporting that as a consumed round would be false, and a
    detector that cries wolf is how the previous silence was earned."""
    from stackowl.pipeline.budget.callback import round_is_outsized

    assert round_is_outsized(before=100_000, after=100_000) is None   # no movement
    assert round_is_outsized(before=100_000, after=900_000) is None   # caps raised


def test_it_is_silent_when_there_is_no_budget_to_measure() -> None:
    """No cap, or no meter, means no number — never a guessed one."""
    from stackowl.pipeline.budget.callback import round_is_outsized

    assert round_is_outsized(before=None, after=100) is None
    assert round_is_outsized(before=100, after=None) is None
    assert round_is_outsized(before=0, after=0) is None


def test_the_threshold_means_fewer_than_four_rounds_remain() -> None:
    """The number has to mean something an operator can act on: at exactly the
    threshold, three more rounds of this size fit and the fourth ends the turn."""
    from stackowl.pipeline.budget.callback import _ROUND_SHARE_ALARM, round_is_outsized

    assert _ROUND_SHARE_ALARM == 0.25
    assert round_is_outsized(before=100_000, after=75_000) is not None   # exactly 25%
    assert round_is_outsized(before=100_000, after=75_001) is None       # just under
