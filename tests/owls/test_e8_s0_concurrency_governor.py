"""E8-S0 — ConcurrencyGovernor: bounds in-flight runs; never leaks a permit."""

from __future__ import annotations

import asyncio

import pytest

from stackowl.owls.concurrency import ConcurrencyGovernor, GovernorSaturatedError
from stackowl.owls.delegation_limits import (
    MAX_CONCURRENT_DELEGATIONS,
    MAX_DELEGATION_DEPTH,
    MAX_INFLIGHT_PIPELINES,
)


class TestDelegationLimits:
    def test_constants_have_expected_values(self) -> None:
        """Depth/width raised 2026-07-22 (owner decision); MAX_INFLIGHT_PIPELINES
        is the physical host ceiling and is unchanged."""
        assert MAX_DELEGATION_DEPTH == 6
        assert MAX_INFLIGHT_PIPELINES == 4
        assert MAX_CONCURRENT_DELEGATIONS == 12


class TestConcurrencyGovernor:
    def test_rejects_non_positive_budget(self) -> None:
        with pytest.raises(ValueError):
            ConcurrencyGovernor(max_inflight=0)
        with pytest.raises(ValueError):
            ConcurrencyGovernor(max_inflight=-1)

    async def test_in_flight_starts_at_zero(self) -> None:
        gov = ConcurrencyGovernor(max_inflight=4)
        assert gov.in_flight == 0
        assert gov.max_inflight == 4

    async def test_slot_acquire_and_release_tracks_in_flight(self) -> None:
        gov = ConcurrencyGovernor(max_inflight=4)
        async with gov.slot():
            assert gov.in_flight == 1
        assert gov.in_flight == 0

    async def test_bounds_n_concurrent_acquirers(self) -> None:
        gov = ConcurrencyGovernor(max_inflight=2)
        entered = asyncio.Event()
        release = asyncio.Event()
        active = 0
        peak = 0

        async def worker() -> None:
            nonlocal active, peak
            async with gov.slot():
                active += 1
                peak = max(peak, active)
                entered.set()
                await release.wait()
                active -= 1

        # Start 4 workers against a budget of 2 → only 2 may hold a slot at once.
        tasks = [asyncio.create_task(worker()) for _ in range(4)]
        await entered.wait()
        await asyncio.sleep(0.02)  # let the scheduler attempt all acquisitions
        assert gov.in_flight == 2  # saturated at the budget, not 4
        assert active == 2
        release.set()
        await asyncio.gather(*tasks)
        assert peak == 2  # never exceeded the budget
        assert gov.in_flight == 0  # all permits returned

    async def test_permit_released_on_exception(self) -> None:
        """A task that raises INSIDE the slot must still release its permit."""
        gov = ConcurrencyGovernor(max_inflight=1)

        async def boom() -> None:
            async with gov.slot():
                assert gov.in_flight == 1
                raise RuntimeError("specialist crashed inside the slot")

        with pytest.raises(RuntimeError):
            await boom()
        # Self-healing: the crash must not leak a permit.
        assert gov.in_flight == 0
        # And the freed budget must be reusable.
        async with gov.slot():
            assert gov.in_flight == 1
        assert gov.in_flight == 0

    async def test_bounded_acquire_raises_when_saturated(self) -> None:
        """A bounded slot(timeout=) fails fast (GovernorSaturatedError) under
        saturation instead of deadlocking — the acquire-while-holding fix."""
        gov = ConcurrencyGovernor(max_inflight=1)
        held = asyncio.Event()

        async def holder() -> None:
            async with gov.slot():
                held.set()
                await asyncio.sleep(5)

        task = asyncio.create_task(holder())
        await held.wait()
        assert gov.in_flight == 1
        # Second acquirer with a tiny timeout can't get the only permit → raises.
        with pytest.raises(GovernorSaturatedError):
            async with gov.slot(timeout=0.05):
                pass
        # The failed acquire did NOT consume/leak a permit.
        assert gov.in_flight == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert gov.in_flight == 0

    async def test_unbounded_acquire_still_waits(self) -> None:
        """timeout=None preserves the original indefinite-wait behaviour."""
        gov = ConcurrencyGovernor(max_inflight=1)
        async with gov.slot():  # no timeout
            assert gov.in_flight == 1
        assert gov.in_flight == 0

    async def test_permit_released_on_cancel(self) -> None:
        """A cancelled task holding a slot must release its permit."""
        gov = ConcurrencyGovernor(max_inflight=1)
        holding = asyncio.Event()

        async def holder() -> None:
            async with gov.slot():
                holding.set()
                await asyncio.sleep(60)  # cancelled before this returns

        task = asyncio.create_task(holder())
        await holding.wait()
        assert gov.in_flight == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Permit returned despite the cancellation.
        assert gov.in_flight == 0
