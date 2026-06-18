"""E8-S0 — parliament + delegator share ONE governor; fan-out is gated by it."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.messaging.a2a import A2AQueue
from stackowl.owls.a2a_delegation import A2ADelegator
from stackowl.owls.concurrency import ConcurrencyGovernor
from stackowl.parliament.orchestrator import ParliamentOrchestrator
from stackowl.parliament.session_store import SessionStore
from stackowl.pipeline.backends.base import OrchestratorBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


@pytest.fixture(autouse=True)
def _disable_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False)


@pytest.fixture()
async def parliament_db(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "parliament.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


class _GatedBackend(OrchestratorBackend):
    """Backend that records concurrent in-flight count to prove gating."""

    def __init__(self, governor: ConcurrencyGovernor, hold: asyncio.Event) -> None:
        self._governor = governor
        self._hold = hold
        self.peak_governor_in_flight = 0

    async def run(self, state: PipelineState) -> PipelineState:
        # Observe the SHARED governor's in_flight while this run is active.
        self.peak_governor_in_flight = max(
            self.peak_governor_in_flight, self._governor.in_flight
        )
        await self._hold.wait()
        chunk = ResponseChunk(
            content=f"r-{state.owl_name}", is_final=True, chunk_index=0,
            trace_id=state.trace_id, owl_name=state.owl_name,
        )
        return state.evolve(responses=(chunk,))


class TestSharedGovernor:
    def test_delegator_and_parliament_receive_same_instance(
        self, parliament_db: DbPool
    ) -> None:
        """ONE governor injected into both → identity, not two budgets."""
        governor = ConcurrencyGovernor(max_inflight=4)
        services = StepServices(delegation_governor=governor)
        delegator = A2ADelegator(
            a2a_queue=A2AQueue(), services=services, timeout_seconds=5.0
        )
        hold = asyncio.Event()
        backend = _GatedBackend(governor, hold)
        parliament = ParliamentOrchestrator(
            backend=backend,
            session_store=SessionStore(parliament_db),
            delegation_governor=governor,
            max_rounds=1,
        )

        # The delegator reads the governor off the SAME services instance.
        assert delegator._services.delegation_governor is governor
        # The parliament fan-out (RoundRunner) holds the SAME instance.
        assert parliament._round_runner._governor is governor
        assert delegator._services.delegation_governor is parliament._round_runner._governor

    async def test_governor_bounds_parliament_fan_out(
        self, parliament_db: DbPool
    ) -> None:
        """A budget of 2 must cap a 4-owl parliament round at 2 concurrent runs."""
        governor = ConcurrencyGovernor(max_inflight=2)
        hold = asyncio.Event()
        backend = _GatedBackend(governor, hold)
        parliament = ParliamentOrchestrator(
            backend=backend,
            session_store=SessionStore(parliament_db),
            delegation_governor=governor,
            max_rounds=1,
        )
        run_task = asyncio.create_task(
            parliament.run("topic", ["a", "b", "c", "d"])
        )
        # Let the fan-out attempt all four; the governor holds two back.
        await asyncio.sleep(0.05)
        assert governor.in_flight <= 2
        hold.set()
        await run_task
        # Never exceeded the shared budget, and all permits returned.
        assert backend.peak_governor_in_flight <= 2
        assert governor.in_flight == 0
