"""One timed-out probe is not evidence that a subsystem is down.

MEASURED 2026-08-31 on the live platform. ``provider_registry``'s health check
answered in ~600ms all day and, seven times, took longer than the aggregator's
5-second cap::

    08:45:10  provider_registry timed out after 5002ms
    09:01:01  provider_registry timed out after 5007ms
    09:49:50  provider_registry timed out after 5002ms
    13:17:48  provider_registry timed out after 5003ms
    13:45:18  provider_registry timed out after 5005ms
    14:07:18  provider_registry timed out after 5002ms
    16:28:58  provider_registry timed out after 5002ms

Each one produced ``UNHEALTHY subsystems detected  down: ['provider_registry']``
and a **critical** Telegram alert, and the next sweep found ok=11/11 and sent the
recovery notice — also critical. Seven identical degraded/recovered pairs in one
day, out of 25 critical operator pages. The provider was never down; the probe
was slow.

WHY NOTHING CAUGHT IT. The sweep already re-collects before alerting, but only
``if attempted`` — that is, only when a subsystem has a registered
``HealableResource`` to recycle first. ``provider_registry`` has no healer, so
``attempted`` is empty, the re-collect is skipped, and a SINGLE five-second sample
becomes a critical page.

THE LIE IS IN THE INSTRUMENT, WHICH IS WHERE IT HAS TO BE FIXED. The aggregator's
timeout branch returned ``status="down"``. A probe that did not answer in five
seconds has told us nothing about the subsystem — only about the probe. Damping
the alarm would hide a real outage just as effectively; re-probing distinguishes
them, because a genuinely dead subsystem fails the second attempt too.

AND IT IS NOT ONLY NOISE. ``is_live()`` returns False on any ``down`` and is the
systemd watchdog gate (F-85) — so a single slow probe was, in principle, an
argument for killing the process.

2026-09-12 — THE SENTENCE ABOVE WAS RIGHT AND THE FIX IT SHIPPED WAS HALF OF ONE.
"a genuinely dead subsystem fails the second attempt too" is true, and so does a
host too busy to schedule the coroutine, so a DOUBLE timeout still did not
distinguish them — it only lowered the rate. The second half is at the bottom of
this file: a non-answer is now ``unknown`` when something ELSE also failed to
answer the same sweep, and ``down`` when it was the lone outlier. See DEBT-314 and
``aggregator._corroborate_non_answers`` for the 40-timeout measurement that decided
which case is which.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from stackowl.health.aggregator import (
    _CONTRIBUTOR_RETRY_TIMEOUT,
    _CONTRIBUTOR_TIMEOUT,
    HealthAggregator,
)
from stackowl.health.status import HealthStatus

pytestmark = pytest.mark.asyncio


class _Probe:
    """A contributor whose answer is scripted per attempt."""

    def __init__(self, name: str, script: list[str]) -> None:
        self._name = name
        self._script = script
        self.attempts = 0

    @property
    def contributor_name(self) -> str:
        return self._name

    async def health_check(self) -> HealthStatus:
        outcome = self._script[min(self.attempts, len(self._script) - 1)]
        self.attempts += 1
        if outcome == "hang":
            # Longer than BOTH windows, derived from the constants rather than
            # a literal: a hand-picked 15s silently became a race the moment the
            # retry window widened to 10s.
            await asyncio.sleep((_CONTRIBUTOR_TIMEOUT + _CONTRIBUTOR_RETRY_TIMEOUT) * 2)
        if outcome == "slow_but_answers":
            # Past the FIRST window, inside the second — the case the whole
            # re-probe exists for, and the one it could not express while both
            # windows were the same length.
            await asyncio.sleep(_CONTRIBUTOR_TIMEOUT * 1.4)
            return HealthStatus(
                name=self._name, status="ok", message=None, latency_ms=1.0,
            )
        if outcome == "raise":
            raise RuntimeError("the provider registry is genuinely broken")
        return HealthStatus(
            name=self._name, status=outcome, message=None, latency_ms=1.0,
        )


async def _collect(contributor: _Probe) -> HealthStatus:
    agg = HealthAggregator()
    agg.register(contributor)
    return (await agg.collect())[0]


async def test_a_probe_that_is_slow_ONCE_is_not_reported_down() -> None:
    """The live provider_registry case, seven times on 2026-08-31."""
    probe = _Probe("provider_registry", ["hang", "ok"])

    status = await _collect(probe)

    assert status.status == "ok", (
        "one 5s timeout produced a critical operator page seven times in a day "
        "for a subsystem that answered fine on the next sweep"
    )
    assert probe.attempts == 2


async def test_a_subsystem_that_is_REALLY_gone_is_still_reported_down() -> None:
    """The direction that must not regress. A dead subsystem fails both attempts,
    so this costs an outage one extra probe interval and nothing else."""
    probe = _Probe("provider_registry", ["hang", "hang"])

    status = await _collect(probe)

    assert status.status == "down"
    assert probe.attempts == 2
    assert status.message is not None and "twice" in status.message.lower()


async def test_an_EXCEPTION_is_evidence_and_is_not_re_probed() -> None:
    """A raise says something about the subsystem; a timeout says something about
    the probe. Only the second is a non-answer, so only the second is retried."""
    probe = _Probe("db_pool", ["raise", "ok"])

    status = await _collect(probe)

    assert status.status == "down"
    assert probe.attempts == 1, "an exception must not be second-guessed"


async def test_a_healthy_contributor_is_probed_exactly_ONCE() -> None:
    """The sweep runs every five minutes against eleven contributors. The retry
    must cost nothing on the path that is taken almost every time."""
    probe = _Probe("db_pool", ["ok"])

    status = await _collect(probe)

    assert status.status == "ok"
    assert probe.attempts == 1


async def test_a_DEGRADED_answer_is_taken_at_face_value() -> None:
    """Degraded is an answer, not a silence."""
    probe = _Probe("kuzu", ["degraded"])

    status = await _collect(probe)

    assert status.status == "degraded"
    assert probe.attempts == 1


async def test_the_retry_is_BOUNDED() -> None:
    """Worst case is the two windows, not an unbounded wait — the sweep must
    still finish inside the scheduler's handler ceiling (1200s, so the margin is
    enormous; the point is that it is BOUNDED, not that it is short)."""
    probe = _Probe("provider_registry", ["hang", "hang"])
    loop = asyncio.get_running_loop()
    t0 = loop.time()

    await _collect(probe)

    elapsed = loop.time() - t0
    budget = _CONTRIBUTOR_TIMEOUT + _CONTRIBUTOR_RETRY_TIMEOUT + 2.0
    assert elapsed < budget, f"took {elapsed:.1f}s, budget {budget:.1f}s"


async def test_the_SECOND_window_is_wider_than_the_first() -> None:
    """THE FIX. Re-probing with the identical cap asks the same question twice.

    MEASURED 2026-09-06: 15 subsystems were called `down` after missing both
    windows, and SIX were fully healthy again at the next sweep ~5 minutes later.
    `store_cadence` answers in a median of 423ms and was cut off at exactly 5.0s
    twice; a box under load needs a wider second window to answer at all.
    """
    assert _CONTRIBUTOR_RETRY_TIMEOUT > _CONTRIBUTOR_TIMEOUT


async def test_a_probe_past_the_first_window_but_inside_the_second_is_NOT_down() -> None:
    """The case the re-probe exists for, which it could not previously express."""
    probe = _Probe("store_cadence", ["slow_but_answers", "slow_but_answers"])

    status = await _collect(probe)

    assert status.status == "ok", status.message
    assert probe.attempts == 2, "the re-probe should have been the answering one"


async def test_liveness_is_not_tripped_by_ONE_slow_probe() -> None:
    """is_live() gates the systemd watchdog. A single slow probe was an argument
    for killing the process."""
    agg = HealthAggregator()
    agg.register(_Probe("provider_registry", ["hang", "ok"]))

    assert await agg.is_live() is True


async def test_liveness_still_falls_for_a_subsystem_that_is_really_down() -> None:
    agg = HealthAggregator()
    agg.register(_Probe("provider_registry", ["hang", "hang"]))

    assert await agg.is_live() is False


# --------------------------------------------------------------------------- #
# The EFFECT has to be visible, not just the attempt (DEBT-132's closing check).
# --------------------------------------------------------------------------- #


async def test_a_reprobe_that_answers_BEYOND_the_first_window_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The only evidence that the WIDER window did anything.

    DEBT-132's closing check grepped the line announcing the re-probe, which marks
    the ATTEMPT. MEASURED 2026-09-07: it reported CLOSEABLE on 14 hits and every one
    of those sweeps still ended `ok=14 total=15` — all 14 were a genuinely
    unreachable provider. The wider window fired fourteen times and rescued nothing,
    while the check said the claim was evidenced.

    This is the case that can only happen because the second window is wider: the
    probe answers after MORE than the first window, so the old code would have
    called it down.
    """
    probe = _Probe("provider_registry", ["hang", "slow_but_answers"])

    with caplog.at_level(logging.INFO, logger="stackowl.health"):
        status = await _collect(probe)

    assert status.status == "ok"
    saved = [r for r in caplog.records if "wider window is what saved it" in r.getMessage()]
    assert saved, (
        "a re-probe answered beyond the first window and nothing recorded it, so the "
        "only claim DEBT-132 makes cannot be evidenced from production logs:\n"
        + "\n".join(r.getMessage() for r in caplog.records)
    )


async def test_a_reprobe_that_answers_INSIDE_the_first_window_does_NOT_say_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The other direction, and it is what gives the sentence meaning.

    A line that fires on EVERY successful re-probe would be the same defect one step
    over: present, at INFO, emitted only by new code — and still unable to tell the
    wider window's work from the old window's. The first attempt times out; the
    second answers at once, which the 5s window would also have caught.
    """
    probe = _Probe("provider_registry", ["hang", "ok"])

    with caplog.at_level(logging.INFO, logger="stackowl.health"):
        status = await _collect(probe)

    assert status.status == "ok"
    assert not [
        r for r in caplog.records if "wider window is what saved it" in r.getMessage()
    ], "the discriminating sentence fired for a re-probe the OLD window would have caught"
    assert [
        r for r in caplog.records if "answered on the re-probe after" in r.getMessage()
    ], "the ordinary success line lost its duration"


# --------------------------------------------------------------------------- #
# Corroboration — what a non-answer MEANT, decided by the rest of the sweep
#
# The re-probe above separates a slow probe from a dead subsystem, and it leaves
# one case unseparated: a probe that misses BOTH windows because the HOST was too
# busy to schedule it. Until 2026-09-12 that was reported `down` like any other,
# which is the same lie one level along — a verdict about a subsystem, built from
# a measurement of the box.
#
# MEASURED 2026-09-12, joining every double timeout in the retained logs to its own
# sweep's `collect: exit` line — 40 timeouts in 29 sweeps:
#
#     1 timeout, and it was the ONLY non-ok subsystem  ....  24 sweeps
#     1 timeout, 1 other non-ok subsystem              ....   5 sweeps
#     2 / 4 / 5 timeouts together                      ....   3 sweeps  (11 timeouts)
#
# So 24 of 40 fired while fourteen other contributors answered inside the same
# window — a busy box does not answer fourteen probes and drop one. Calling ALL
# forty `unknown`, which is what this change first set out to do, would have
# retired the detection of the exact failure `is_live` exists for.
# --------------------------------------------------------------------------- #


async def test_a_LONE_non_answer_is_still_down() -> None:
    """24 of the 40 measured cases, and the recorded decision this must not break.

    A subsystem that misses both windows while everything else answers is the
    outlier, and the sweep is evidence about it. `test_a_subsystem_that_is_REALLY_
    gone_is_still_reported_down` above pins the same rule at one contributor.
    """
    agg = HealthAggregator()
    agg.register(_Probe("provider_registry", ["hang", "hang"]))
    for i in range(3):
        agg.register(_Probe(f"answers_{i}", ["ok"]))

    statuses = await agg.collect()
    by_name = {s.name: s for s in statuses}

    assert by_name["provider_registry"].status == "down"
    assert "only subsystem that did not answer" in (by_name["provider_registry"].message or "")
    assert await agg.is_live() is False


async def test_TWO_non_answers_in_one_sweep_are_UNKNOWN_not_down() -> None:
    """11 of the 40, in three sweeps. Nothing measured these subsystems.

    The live shape: on 2026-09-10 five contributors missed both windows in one
    sweep and the platform paged an operator about five simultaneous outages. The
    box was loaded; nothing was broken.
    """
    agg = HealthAggregator()
    agg.register(_Probe("provider_registry", ["hang", "hang"]))
    agg.register(_Probe("store_cadence", ["hang", "hang"]))
    agg.register(_Probe("answers", ["ok"]))

    statuses = await agg.collect()
    unanswered = [s for s in statuses if s.status == "unknown"]

    assert sorted(s.name for s in unanswered) == ["provider_registry", "store_cadence"]
    assert all("timed out twice" in (s.message or "") for s in unanswered)


async def test_an_UNCORROBORATED_sweep_does_not_kill_the_process() -> None:
    """THE POINT OF THE STATE, and the half `is_live` had to be taught.

    A restart is the worst possible response to a loaded box, because the restart
    is itself load. `unknown` is deliberately absent from LIVENESS_FAILING_STATES.
    """
    agg = HealthAggregator()
    agg.register(_Probe("provider_registry", ["hang", "hang"]))
    agg.register(_Probe("store_cadence", ["hang", "hang"]))

    assert await agg.is_live() is True


async def test_the_operator_is_still_told_about_an_unmeasured_subsystem() -> None:
    """Quiet is NOT the alternative to a false outage.

    `unknown` sits in WARNING_STATES, so the sweep still alerts — it just stops
    calling it an outage. A state in no bucket would be counted HEALTHY by
    `health_sweep`, silently, which is the failure this whole change guards.
    """
    from stackowl.health.status import LIVENESS_FAILING_STATES, WARNING_STATES

    assert "unknown" in WARNING_STATES
    assert "unknown" not in LIVENESS_FAILING_STATES
