"""D04.6 — the detector that notices flapping was written, tested, and never run.

TWO DEFECTS, and the second is why wiring alone would have shipped decoration.

**It was never constructed.** `ResilienceContributor` had ZERO construction sites
in `src/` — only tests. Two files record the deferral in prose: `cli/app.py` (the
out-of-process CLI has no live resource handles, and says so) and
`scheduler/handlers/health_sweep.py`. So the one component that could see a
subsystem flapping was never asked, and nothing else measures contention:
`DbContributor` computes `latency_ms` and never thresholds it, so a pool sitting
on a 15-second `busy_timeout` reports `ok`.

**And it could not have detected anything anyway.** Status was
`"degraded" if any_unavailable else "ok"`; `recycle_count` reached only a MESSAGE
STRING. A pool that recycled fifty times and recovered each time reported
`ok(recycles=50)` — a number no status ever depended on.

WHY A RATE AND NOT A COUNT. The obvious repair — degrade above N cumulative
recycles — is CLAUDE.md's defect shape #4, "no decay": a monotonic counter on a
long-lived process crosses any fixed threshold eventually and then latches
degraded forever, and an alarm that never clears is one the operator learns to
ignore. This measures the DELTA between sweeps instead, so the signal is
"flapping now", not "has ever flapped". Measured baseline: recycle incidents run
~9.5/day, roughly 0.03 per 5-minute sweep, so two in one interval is genuinely
abnormal rather than routine self-healing.
"""

from __future__ import annotations

import pathlib

import pytest

from stackowl.health.contributors import ResilienceContributor

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "stackowl"


class _Res:
    """Stands in for a HealableResource (DbPool exposes exactly these three)."""

    def __init__(self, *, available: bool = True, recycle_count: int = 0) -> None:
        self.available = available
        self.recycle_count = recycle_count
        self.unavailable_reason = None if available else "connection dropped"


@pytest.mark.asyncio
async def test_the_first_sweep_only_establishes_a_baseline() -> None:
    """A process that has been up for a week starts with a large cumulative
    count. Alarming on it would fire on every boot, for history."""
    pool = _Res(recycle_count=137)
    contributor = ResilienceContributor({"db_pool": pool})

    status = await contributor.health_check()

    assert status.status == "ok", (
        "the first observation alarmed on an accumulated count — that fires on "
        "every restart, about recycles that already healed"
    )


@pytest.mark.asyncio
async def test_flapping_between_sweeps_is_degraded() -> None:
    """The behaviour the contributor exists for, and never had."""
    pool = _Res(recycle_count=0)
    contributor = ResilienceContributor({"db_pool": pool})
    await contributor.health_check()          # baseline

    pool.recycle_count = 2                    # two recycles in one sweep interval
    status = await contributor.health_check()

    assert status.status == "degraded", (
        "a pool that recycled twice between sweeps reported ok. recycle_count "
        "reaching only the message string is what made this detector blind."
    )
    assert "db_pool" in status.message


@pytest.mark.asyncio
async def test_a_single_recycle_is_the_self_heal_working() -> None:
    """One recycle is the system healing itself, which is a success, not an
    incident. Alarming on it would train the operator to ignore the alarm."""
    pool = _Res(recycle_count=0)
    contributor = ResilienceContributor({"db_pool": pool})
    await contributor.health_check()

    pool.recycle_count = 1
    assert (await contributor.health_check()).status == "ok"


@pytest.mark.asyncio
async def test_the_alarm_CLEARS_when_the_flapping_stops() -> None:
    """THE NO-DECAY GUARD. A cumulative threshold would stay degraded forever
    after one bad afternoon."""
    pool = _Res(recycle_count=0)
    contributor = ResilienceContributor({"db_pool": pool})
    await contributor.health_check()

    pool.recycle_count = 9                     # a burst
    assert (await contributor.health_check()).status == "degraded"

    # ...and then it settles. The count stays high; the RATE goes to zero.
    assert (await contributor.health_check()).status == "ok", (
        "still degraded after the flapping stopped — the alarm latched on a "
        "monotonic count, which is exactly the decay defect this avoids"
    )


@pytest.mark.asyncio
async def test_an_unavailable_resource_is_still_degraded() -> None:
    """Regression guard on the one thing it always did correctly."""
    contributor = ResilienceContributor({"db_pool": _Res(available=False)})
    status = await contributor.health_check()

    assert status.status == "degraded"
    assert "DOWN" in status.message


@pytest.mark.tripwire
def test_the_detector_is_actually_CONSTRUCTED_in_production() -> None:
    """A detector nobody builds is decoration, and this one was decoration for
    its whole life. The guard is structural because no runtime test can prove a
    registration that only happens inside the serve process's assembly.
    """
    assembly = (_SRC / "scheduler" / "assembly.py").read_text(encoding="utf-8")

    assert "ResilienceContributor(" in assembly, (
        "ResilienceContributor is not constructed in the in-process health "
        "assembly. It was written, tested and never run once — if that is "
        "intended, the standing rule is that retired means DELETED, not parked."
    )
