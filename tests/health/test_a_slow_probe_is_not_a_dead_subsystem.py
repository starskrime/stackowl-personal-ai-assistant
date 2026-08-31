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
timeout branch returns ``status="down"``. A probe that did not answer in five
seconds has told us nothing about the subsystem — only about the probe. Damping
the alarm would hide a real outage just as effectively; re-probing distinguishes
them, because a genuinely dead subsystem fails the second attempt too.

AND IT IS NOT ONLY NOISE. ``is_live()`` returns False on any ``down`` and is the
systemd watchdog gate (F-85) — so a single slow probe was, in principle, an
argument for killing the process.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.health.aggregator import (
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
            await asyncio.sleep(_CONTRIBUTOR_TIMEOUT * 3)
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


async def test_the_retry_is_BOUNDED_by_the_same_timeout() -> None:
    """Worst case is two probe windows, not an unbounded wait — the sweep must
    still finish inside the scheduler's handler ceiling."""
    probe = _Probe("provider_registry", ["hang", "hang"])
    loop = asyncio.get_running_loop()
    t0 = loop.time()

    await _collect(probe)

    elapsed = loop.time() - t0
    assert elapsed < _CONTRIBUTOR_TIMEOUT * 2 + 2.0, f"took {elapsed:.1f}s"


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
