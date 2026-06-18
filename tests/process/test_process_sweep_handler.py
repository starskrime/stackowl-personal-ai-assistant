"""ProcessSweepHandler — the dispatchable scheduler seam (E9-S0).

The registry's sweep logic is covered in ``test_registry.py``; this proves the
JobHandler the scheduler ACTUALLY dispatches: the factory registers it on the
process ``HandlerRegistry`` (so ``scheduler.get("process_sweep")`` resolves), and
``execute`` drives ``ProcessRegistry.sweep`` once and reports the counts — and
self-heals (never raises into the scheduler loop) when the sweep fails.
"""

from __future__ import annotations

import pytest

from stackowl.process.checkpoint import ProcessCheckpoint
from stackowl.process.registry import ProcessRegistry
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.handlers.process_sweep import (
    ProcessSweepHandler,
    register_process_sweep_handler,
)
from stackowl.scheduler.job import Job

from .conftest import FakeClock, py


def _registry(clock: FakeClock, tmp_path) -> ProcessRegistry:
    return ProcessRegistry(clock=clock, checkpoint=ProcessCheckpoint(path=tmp_path / "proc.json"))


def _job() -> Job:
    return Job(
        job_id="job-process-sweep",
        handler_name="process_sweep",
        schedule="every 10m",
        idempotency_key="k",
        last_run_at=None,
        next_run_at="2026-01-01T00:00:00Z",
        status="pending",
    )


@pytest.fixture(autouse=True)
def _clean_handler_registry():  # noqa: ANN202
    """The HandlerRegistry is a process-singleton — reset around each test."""
    HandlerRegistry.reset()
    yield
    HandlerRegistry.reset()


def test_factory_registers_on_handler_registry(clock: FakeClock, tmp_path) -> None:
    reg = _registry(clock, tmp_path)
    handler = register_process_sweep_handler(reg)
    # The scheduler resolves the handler by name off the SAME singleton it dispatches from.
    resolved = HandlerRegistry.instance().get("process_sweep")
    assert resolved is handler
    assert handler.handler_name == "process_sweep"


@pytest.mark.asyncio
async def test_execute_drives_sweep_and_auto_kills_overdue(clock: FakeClock, tmp_path) -> None:
    reg = _registry(clock, tmp_path)
    handle = await reg.start(py("import time; time.sleep(30)"), session_id="s1")
    handler = ProcessSweepHandler(reg)

    # Before the mandatory TTL elapses, the sweep kills nothing.
    res = await handler.execute(_job())
    assert res.success
    assert res.metadata["auto_killed"] == 0

    # Past the mandatory lifetime, execute's sweep auto-kills the overdue process.
    clock.advance(3601.0)
    res = await handler.execute(_job())
    assert res.success
    assert res.metadata["auto_killed"] == 1
    assert "auto_killed=1" in (res.output or "")
    polled = await reg.poll(handle.process_id, "s1")
    assert polled is not None and not polled.is_running


@pytest.mark.asyncio
async def test_execute_self_heals_when_sweep_raises() -> None:
    """A misbehaving registry must never crash the scheduler loop (self-healing)."""

    class _BoomRegistry:
        async def sweep(self, now: float | None = None) -> dict[str, int]:
            raise RuntimeError("sweep boom")

    handler = ProcessSweepHandler(_BoomRegistry())  # type: ignore[arg-type]
    res = await handler.execute(_job())
    # No raise; reported as a benign no-op (zeroed counts), success so the job reschedules.
    assert res.success
    assert res.metadata == {"auto_killed": 0, "pruned": 0, "evicted": 0}
