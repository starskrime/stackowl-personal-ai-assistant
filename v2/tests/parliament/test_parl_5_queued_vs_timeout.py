"""PARL-5 (F087) — a saturated host reports 'queued out', not '[timed out]'.

The governor slot is acquired with a BOUNDED acquire timeout, separate from the
per-owl run budget. An owl that never gets a slot (host saturated) must report a
distinct 'queued' marker — not the '[timed out after Ns]' that a genuinely-slow
RUN produces — so the operator can tell "never ran" from "too slow".
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.owls.concurrency import ConcurrencyGovernor
from stackowl.parliament.models import ParliamentSession
from stackowl.parliament.round_runner import RoundRunner


class _SlowBackend:
    """A backend that takes longer than the per-owl run budget."""

    async def run(self, state: object) -> object:
        await asyncio.sleep(5.0)
        return state


class _BlockingBackend:
    """Holds its governor slot until released — used to saturate the budget."""

    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def run(self, state: object) -> object:
        await self.release.wait()
        return state


def _session(owls: list[str]) -> ParliamentSession:
    return ParliamentSession(topic="t", owl_names=owls)


@pytest.mark.asyncio
async def test_slow_run_reports_timed_out() -> None:
    runner = RoundRunner(
        backend=_SlowBackend(),  # type: ignore[arg-type]
        per_owl_timeout_s=0.05,
        token_budget=10_000,
    )
    session = _session(["scout"])
    rnd = await runner.run_round(session, 1, {"scout": "go"})
    assert "[timed out" in rnd.responses["scout"]
    assert rnd.truncated["scout"] is True


@pytest.mark.asyncio
async def test_queued_out_reports_distinct_marker() -> None:
    # Governor with ONE permit; a blocking owl holds it so the second owl can
    # never acquire within the bounded acquire timeout → 'queued', not timeout.
    governor = ConcurrencyGovernor(max_inflight=1)
    blocker = _BlockingBackend()
    runner = RoundRunner(
        backend=blocker,  # type: ignore[arg-type]
        per_owl_timeout_s=5.0,  # generous RUN budget — the run never starts
        token_budget=10_000,
        delegation_governor=governor,
        acquire_timeout_s=0.1,  # tiny ACQUIRE budget — the slot is taken
    )
    session = _session(["holder", "waiter"])

    async def _release_soon() -> None:
        await asyncio.sleep(0.4)
        blocker.release.set()

    asyncio.create_task(_release_soon())
    rnd = await runner.run_round(session, 1, {"holder": "a", "waiter": "b"})

    # At least one owl was queued out; its marker is distinct from '[timed out'.
    queued = [t for t in rnd.responses.values() if "[queued out" in t]
    assert queued, f"expected a queued-out marker, got {rnd.responses}"
    for text in queued:
        assert "[timed out" not in text
