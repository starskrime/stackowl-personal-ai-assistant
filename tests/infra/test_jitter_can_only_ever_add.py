"""D04.6 — jitter decorrelates self-computed retries and can never shorten a wait.

ADOPTED ON THE OPERATOR'S CALL, against the evidence, and the record says so.
Measured 2026-09-05: in the platform's entire history exactly FOUR task rows ever
reached attempt 2, and the 7-wide "herd" that appeared to justify jitter was a
fossil — unreachable-owner rows filed under a non-principal owner, which
`claimable()` (owner-scoped) could never claim. They contended for nothing. So
this ships as a hedge against a scale-out risk that today's data does not show,
not as a fix for a measured harm. Recording that honestly is the point; the
premise_check in `progress.yml` says what would make it corrective.

THE ONE PROPERTY THAT MAKES IT SAFE IS ADDITIVE-ONLY. `jittered(d) >= d`, always.
A retry delay in this platform is not always ours to choose: `retry_actuator`
returns `exc.retry_after + buffer`, and the Telegram flood guard sets a deadline
the server dictated. This programme has already paid for a ~10-hour flood ban
(2026-07-19). A jitter that could subtract would re-earn it.

AND THE FIELD IT MUST NEVER TOUCH IS THE BREAKER'S. `CircuitBreaker.
_current_half_open_seconds` carries BOTH the self-computed adaptive doubling AND
the server-mandated cooldown written by `open_for()` — its own docstring says the
reuse is deliberate. Jittering there would shorten a provider's own `Retry-After`,
and it is also what the operator is shown ("[open, retry in Ns]") and what
`CircuitOpenError` carries. So the guard below is not stylistic: it is the
difference between decorrelating our own schedule and lying about a server
contract.
"""

from __future__ import annotations

import pathlib
import random

import pytest

from stackowl.infra.resilience import JITTER_FRACTION, jittered

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "stackowl"


@pytest.mark.parametrize("delay", [0.0, 0.001, 1.0, 5.0, 60.0, 900.0])
def test_jitter_can_only_add(delay: float) -> None:
    """The safety property, over enough draws to catch a sign error."""
    rng = random.Random(20260905)
    for _ in range(500):
        out = jittered(delay, rng=rng)
        assert out >= delay, f"jitter SHORTENED {delay} to {out}"
        assert out <= delay * (1.0 + JITTER_FRACTION) + 1e-9, f"jitter overshot: {out}"


def test_jitter_actually_decorrelates() -> None:
    """Otherwise it is a no-op wearing a helper's clothes — and the whole point
    is that N retriers computing the same delay stop waking together."""
    rng = random.Random(7)
    draws = {jittered(60.0, rng=rng) for _ in range(200)}
    assert len(draws) > 100, f"only {len(draws)} distinct delays — not decorrelating"


def test_a_zero_fraction_is_byte_identical_to_today() -> None:
    """The opt-out has to be exact, or 'set it to 0 to reproduce' is a lie.
    This is what makes the change reversible by configuration."""
    for d in (0.0, 5.0, 15.0, 900.0):
        assert jittered(d, fraction=0.0) == d


def test_a_nonsense_delay_does_not_become_a_nonsense_wait() -> None:
    """A negative delay is a bug upstream; jitter must not amplify it into a
    random one. Fails closed at zero."""
    assert jittered(-5.0) == 0.0
    assert jittered(float("nan")) == 0.0
    assert jittered(float("inf")) == 0.0


@pytest.mark.tripwire
def test_no_server_mandated_delay_is_ever_jittered() -> None:
    """THE GUARD THAT MATTERS. These three carry a duration the SERVER chose.

    A cross-cutting rule like this is exactly what a targeted test run cannot
    see: whoever adds the next jitter call will be editing a retry file, and
    nothing about that edit looks related to the Telegram adapter.
    """
    forbidden = {
        "providers/circuit_breaker.py":
            "_current_half_open_seconds carries the server's Retry-After via open_for()",
        "pipeline/retry_actuator.py":
            "_delivery_retry_delay_seconds returns the server's retry_after + buffer",
        "channels/telegram/adapter.py":
            "_flood_until is a deadline the server dictated — the ~10h ban of 2026-07-19",
    }
    offenders = []
    for rel, why in forbidden.items():
        text = (_SRC / rel).read_text(encoding="utf-8")
        if "jittered(" in text or "from stackowl.infra.resilience import jittered" in text:
            offenders.append(f"{rel}: {why}")

    assert not offenders, (
        "a server-mandated delay is being jittered:\n  " + "\n  ".join(offenders) +
        "\nJitter is additive-only so it cannot shorten the wait — but these are "
        "durations a provider NAMED, and lengthening them silently is still the "
        "platform disagreeing with a contract it was given. Jitter our own "
        "schedules only."
    )
