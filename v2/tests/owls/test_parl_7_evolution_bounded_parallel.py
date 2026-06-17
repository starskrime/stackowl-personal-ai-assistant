"""PARL-7 (F084) — one stuck owl can't stall the nightly evolution batch.

Per-owl evolution is bounded by a timeout and the owls are evolved concurrently
under the shared ConcurrencyGovernor, so a single hung owl's LLM call cannot block
the evolution of every other owl in the batch.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from stackowl.owls.concurrency import ConcurrencyGovernor
from stackowl.owls.evolution import EvolutionCoordinator
from stackowl.scheduler.job import Job


def _job(job_id: str = "j1") -> Job:
    return Job(
        job_id=job_id,
        handler_name="evolution_batch",
        schedule="*/10 * * * *",
        idempotency_key=job_id,
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
    )


@dataclass
class _FakeManifest:
    name: str


class _FakeRegistry:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def list(self) -> list[_FakeManifest]:
        return [_FakeManifest(n) for n in self._names]


@pytest.mark.asyncio
async def test_hung_owl_does_not_block_others() -> None:
    names = ["hung", "fast1", "fast2"]
    coord = EvolutionCoordinator(
        db=object(),  # type: ignore[arg-type]
        provider_registry=object(),  # type: ignore[arg-type]
        owl_registry=_FakeRegistry(names),  # type: ignore[arg-type]
        per_owl_timeout_s=0.2,
        delegation_governor=ConcurrencyGovernor(max_inflight=4),
    )

    evolved: list[str] = []

    async def _fake_evolve_one(manifest: _FakeManifest) -> bool:
        if manifest.name == "hung":
            await asyncio.sleep(10.0)  # would hang far past the timeout
            return True
        await asyncio.sleep(0.01)
        evolved.append(manifest.name)
        return True

    coord._evolve_one = _fake_evolve_one  # type: ignore[assignment,method-assign]

    job = _job()
    t0 = asyncio.get_event_loop().time()
    result = await coord.execute(job)
    elapsed = asyncio.get_event_loop().time() - t0

    # The two fast owls evolved despite the hung one; the batch did not hang.
    assert set(evolved) == {"fast1", "fast2"}
    # Bounded: the whole batch finished close to the per-owl timeout, not 10s.
    assert elapsed < 2.0
    # The job still reports success (a single owl timeout is not a batch failure).
    assert result.success is True
