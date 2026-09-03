"""A reported A/B difference must say whether it can support a conclusion.

WHAT TODAY'S BRIEF TOLD HIM, verbatim (2026-09-03T13:00:11, delivered):

    lessons_success[interactive] injected:79.7%(n=836)  held_out:69.2%(n=133)
    lessons_success[machine]     injected:39.8%(n=7145) held_out:42.4%(n=1463)

Read plainly, the second line says injecting lessons makes machine turns WORSE.
It does not. Computing the two-proportion standard error on the same live rows:

    telegram   +5.6pp   se 2.66pp   2.1 sigma   distinguishable
    rca        -1.6pp   se 1.59pp  -1.0 sigma   noise
    ALL        -0.7pp   se 1.36pp  -0.5 sigma   noise

The one effect that is real — lessons genuinely help his own conversations — is
printed in the same shape, and with the same authority, as two that are wobble.

THE AUTHOR ALREADY NAMED THIS HAZARD, one guard short. The quality line beside it
carries an explicit caveat, and the code comment above the arm check reads: "one
side is not a comparison, and printing it invites a conclusion from noise." That
guard covers the DEGENERATE case — an arm with no rows — and nothing covers the
general one, where both arms have plenty of rows and the difference between them
is smaller than its own sampling error. Same sentence, one case short.

This is the programme's own denominator rule a level up: it is not enough to
publish the numerator and the denominator if the reader cannot tell signal from
sampling. A number that invites a wrong action is worse than no number, because
he would have to re-derive the standard error by hand to know which of the four
lines to believe — and the brief exists so he does not have to.
"""

from __future__ import annotations

import pytest

from stackowl.brief.assemblers import ab_delta_phrase

# The exact live figures from the 2026-09-03 brief and the DB behind it.
LIVE_MACHINE = (2845, 7145, 620, 1463)      # injected ok/n, held_out ok/n  (~39.8% vs 42.4%)
LIVE_INTERACTIVE = (666, 836, 92, 133)      # ~79.7% vs 69.2%


def test_the_machine_line_he_received_is_reported_as_noise() -> None:
    """THE REGRESSION. A -2.6pp gap at 1 sigma read as "lessons hurt"."""
    phrase = ab_delta_phrase(*LIVE_MACHINE)
    assert "noise" in phrase.lower(), phrase
    assert "-" in phrase or "−" in phrase, f"the direction must still be shown: {phrase}"


def test_a_real_effect_is_not_dismissed_as_noise() -> None:
    """The guard must not flatten everything to "cannot tell" — lessons DO help
    his interactive turns, and that is the finding worth acting on."""
    phrase = ab_delta_phrase(*LIVE_INTERACTIVE)
    assert "noise" not in phrase.lower(), phrase
    assert "+" in phrase


def test_the_magnitude_is_always_shown() -> None:
    """Suppressing the number would be the opposite error — he should still see
    how big the difference is, alongside whether it is trustworthy."""
    for args in (LIVE_MACHINE, LIVE_INTERACTIVE):
        assert "pp" in ab_delta_phrase(*args)


def test_a_tiny_sample_is_never_called_real() -> None:
    """Two turns against two turns can produce a 50pp "effect". The sample size
    is exactly what decides, and it must dominate the magnitude."""
    phrase = ab_delta_phrase(2, 2, 1, 2)  # 100% vs 50%, n=2 each
    assert "noise" in phrase.lower(), phrase


def test_a_huge_sample_with_a_small_gap_is_real() -> None:
    """The mirror: a 2pp difference over 100,000 turns a side IS a finding, and a
    rule keyed on magnitude rather than on sigma would throw it away."""
    phrase = ab_delta_phrase(52_000, 100_000, 50_000, 100_000)
    assert "noise" not in phrase.lower(), phrase


@pytest.mark.parametrize(
    ("i_ok", "i_n", "h_ok", "h_n"),
    [(0, 0, 0, 0), (5, 10, 0, 0), (0, 0, 5, 10)],
)
def test_an_empty_arm_never_produces_a_verdict(
    i_ok: int, i_n: int, h_ok: int, h_n: int,
) -> None:
    """One side is not a comparison — the guard that already existed, kept. A
    division by zero here would take the whole brief down."""
    assert ab_delta_phrase(i_ok, i_n, h_ok, h_n) == ""


def test_two_identical_arms_are_noise_not_a_finding() -> None:
    """A zero difference is the clearest case of "nothing to conclude"."""
    assert "noise" in ab_delta_phrase(500, 1000, 500, 1000).lower()
