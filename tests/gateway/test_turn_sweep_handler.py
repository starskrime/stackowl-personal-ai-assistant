"""TurnSweepHandler — the dispatchable scheduler seam for the F050 backstop reaper.

The registry's sweep semantics (done-gated reap, stranded drain) are covered in
``test_turn_registry.py``; this proves the JobHandler the scheduler ACTUALLY
dispatches: the factory registers it on the shared ``HandlerRegistry`` (so
``HandlerRegistry.instance().get("turn_sweep")`` resolves), ``execute`` drives
``TurnRegistry.sweep`` once and reports the reaped count, surfaces a stranded
session, and self-heals (never raises into the scheduler loop) when the sweep fails.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.gateway.turn_registry import TurnRegistry
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.handlers.turn_sweep import (
    TurnSweepHandler,
    register_turn_sweep_handler,
)
from stackowl.scheduler.job import Job


def _job() -> Job:
    return Job(
        job_id="job-turn-sweep",
        handler_name="turn_sweep",
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


def test_factory_registers_on_handler_registry() -> None:
    reg = TurnRegistry()
    handler = register_turn_sweep_handler(reg, ttl_seconds=10.0)
    resolved = HandlerRegistry.instance().get("turn_sweep")
    assert resolved is handler
    assert handler.handler_name == "turn_sweep"


@pytest.mark.asyncio
async def test_handler_drives_sweep_and_reports_reaped() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("stuck", session_id="s1", task=t, target=None, original_input="a")
    await t  # done, status still RUNNING — the reapable wedge
    handler = TurnSweepHandler(reg, ttl_seconds=999_999.0)

    res = await handler.execute(_job())

    assert res.success
    assert res.metadata["reaped"] == 1
    assert "reaped=1" in (res.output or "")
    assert reg.get("stuck") is None


@pytest.mark.asyncio
async def test_handler_does_not_reap_live_turn() -> None:
    reg = TurnRegistry()
    live = asyncio.create_task(asyncio.sleep(60))
    await reg.register("live", session_id="s1", task=live, target=None, original_input="a")
    handler = TurnSweepHandler(reg, ttl_seconds=0.0)  # past TTL but NOT done

    res = await handler.execute(_job())

    assert res.metadata["reaped"] == 0  # a live running turn is NEVER reaped
    assert reg.get("live") is not None
    live.cancel()
    with pytest.raises(asyncio.CancelledError):
        await live


@pytest.mark.asyncio
async def test_reaped_stranded_session_is_drained() -> None:
    """No-fake-success: a reap that frees a slot surfaces the stranded session."""
    calls: list[int] = []

    async def _drainer() -> None:
        calls.append(1)

    reg = TurnRegistry()
    reg.set_stranded_drainer(_drainer)
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("stuck", session_id="s1", task=t, target=None, original_input="a")
    await t
    handler = TurnSweepHandler(reg, ttl_seconds=0.0)

    await handler.execute(_job())

    assert calls == [1]  # reaped AND surfaced to the drain seam


@pytest.mark.asyncio
async def test_handler_self_heals_when_sweep_raises() -> None:
    """A misbehaving registry must never crash the scheduler loop (self-healing)."""

    class _BoomRegistry:
        async def sweep(self, *, ttl_seconds: float) -> list[str]:
            raise RuntimeError("sweep boom")

    handler = TurnSweepHandler(_BoomRegistry(), ttl_seconds=10.0)  # type: ignore[arg-type]
    res = await handler.execute(_job())

    assert res.success  # reported as a benign no-op so the job reschedules
    assert res.metadata == {"reaped": 0}
