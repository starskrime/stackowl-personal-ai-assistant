"""D04.6 — a recorded `rate_limit_penalty` must mean pacing actually happened.

THE DEFECT THIS PINS. On a classified RATE_LIMIT, `resilient_round` calls
`limiter.penalize()` and, in the `else` of that try, records a retry-ledger event
named `rate_limit_penalty`. `penalize()` returns SILENTLY when the bucket is
uncapped — `if self._capacity is None: return`, placed BEFORE its own WARNING — so
nothing was paced, nothing was logged, and the ledger recorded that the platform
slowed itself down.

AND THE UNCAPPED BUCKET IS THE LIVE CONFIGURATION, not an edge case. Measured
2026-09-05: `rate_limit_rpm` is absent from `~/.stackowl/stackowl.yaml`, and
`RateLimiter.from_rpm(None)` returns a pass-through (`capacity=None`). There is one
configured provider and all three tiers resolve to it, so the no-op limiter covers
**100% of traffic**. The existing test that proves the penalty works
(`test_resilient_round_penalizes_limiter_on_rate_limit`) builds a limiter with
`capacity=5` — it exercises a configuration production does not have.

WHY IT MATTERS MORE THAN A WRONG LOG LINE. `penalize` and `open_for` have each
fired ZERO times in nine days of logs. Today that zero is unreadable: it cannot be
told apart from "the path is inert", because a firing would have recorded the same
event either way. `is_noop` — the exact property that distinguishes them — already
exists at `rate_limiter.py:104` with ZERO consumers. Built, correct, never asked.
That is CLAUDE.md shape #5 sitting inside the actuator, and shape #1 (measure the
EFFECT, never trust the CALL) sitting in its record.

The fix is not to invent an RPM. It is that a platform told to slow down, which
cannot slow down, must SAY SO — at INFO, because production runs at INFO.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.infra import retry_ledger
from stackowl.providers._resilient_round import resilient_round
from stackowl.providers.circuit_breaker import CircuitBreaker
from stackowl.providers.rate_limiter import RateLimiter


class _TooManyRequests(Exception):
    status_code = 429


def _raiser(exc: BaseException):
    async def _round() -> None:
        raise exc
    return _round


def test_an_uncapped_limiter_reports_that_it_did_not_pace() -> None:
    """The contract the caller needs: penalize() says whether it acted."""
    limiter = RateLimiter(provider_name="p", capacity=None, refill_rate=None)
    assert limiter.is_noop is True

    applied = limiter.penalize()

    assert applied is False, (
        "an uncapped limiter reported that it paced. It cannot: there is no refill "
        "rate to shrink. Returning None/True here is what let the ledger record a "
        "back-pressure response that never happened."
    )
    assert limiter._penalty_until == 0.0


def test_a_capped_limiter_still_paces_and_says_so() -> None:
    """The positive control. Without it, the assertion above passes vacuously
    for a limiter that never paces at all."""
    limiter = RateLimiter(provider_name="p", capacity=5, refill_rate=1.0)

    applied = limiter.penalize(factor=0.5, duration_seconds=30.0)

    assert applied is True
    assert limiter._penalty_until > 0.0
    assert limiter._penalty_factor == 0.5


def test_being_unable_to_pace_is_visible_at_INFO(caplog: pytest.LogCaptureFixture) -> None:
    """Production runs at INFO. A DEBUG line here could never close the question
    of whether the rate-limit path is inert — the failure this programme has
    already paid for once."""
    limiter = RateLimiter(provider_name="p", capacity=None, refill_rate=None)

    with caplog.at_level(logging.INFO):
        limiter.penalize()

    hits = [r for r in caplog.records if "cannot pace" in r.getMessage()]
    assert hits, "an uncapped limiter declined silently — nothing tells the operator"
    assert hits[0].levelno >= logging.INFO, (
        f"the only evidence is at {hits[0].levelname}; production runs at INFO"
    )


@pytest.mark.asyncio
async def test_the_ledger_does_not_claim_a_penalty_that_never_happened() -> None:
    """The whole point: the RECORD must match the EFFECT."""
    breaker = CircuitBreaker(provider_name="p")
    limiter = RateLimiter(provider_name="p", capacity=None, refill_rate=None)

    token = retry_ledger.bind()
    try:
        with pytest.raises(_TooManyRequests):
            await resilient_round(breaker, limiter, _raiser(_TooManyRequests()))
        kinds = [e.kind for e in retry_ledger.get_retry()]
    finally:
        retry_ledger.reset(token)

    assert "rate_limit_penalty" not in kinds, (
        "the ledger recorded `rate_limit_penalty` against an uncapped limiter — a "
        "claim that the platform slowed down while it did nothing at all. This is "
        "the live configuration, so every real 429 would be recorded this way."
    )
    assert "rate_limit_unpaced" in kinds, (
        "silence is not the fix either. Being told to slow down and having no way "
        "to do it is exactly the fact an operator needs recorded."
    )


@pytest.mark.asyncio
async def test_a_configured_limiter_still_records_a_real_penalty() -> None:
    """Regression guard on the path that DOES work — the honesty fix must not
    quietly disarm pacing for an operator who did configure an RPM."""
    breaker = CircuitBreaker(provider_name="p")
    limiter = RateLimiter(provider_name="p", capacity=5, refill_rate=1.0)

    token = retry_ledger.bind()
    try:
        with pytest.raises(_TooManyRequests):
            await resilient_round(breaker, limiter, _raiser(_TooManyRequests()))
        kinds = [e.kind for e in retry_ledger.get_retry()]
    finally:
        retry_ledger.reset(token)

    assert "rate_limit_penalty" in kinds
    assert "rate_limit_unpaced" not in kinds
    assert limiter._penalty_until > 0.0
