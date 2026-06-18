"""Tests for :class:`ClarifySweepHandler` — the recurring expired-clarify reaper.

Covers: ``handler_name`` is ``"clarify_sweep"``; ``execute`` drives
``ClarifyGateway.sweep_expired(ttl)`` and reports the dropped count (stale
turn-yield entries aged past the TTL via an injected clock are dropped);
``register_clarify_sweep_handler`` puts the handler on the process registry; the
handler never raises even when the gateway misbehaves (self-healing → 0 dropped).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from stackowl.interaction.clarify_gateway import ClarifyGateway
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.handlers.clarify_sweep import (
    ClarifySweepHandler,
    register_clarify_sweep_handler,
)
from stackowl.scheduler.job import Job, JobResult


class _FakeClock:
    """Monotonic-shaped injectable clock so TTL/expiry tests do not sleep."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _ExplodingGateway:
    """Stand-in whose ``sweep_expired`` raises — proves the handler self-heals."""

    def sweep_expired(self, ttl_seconds: float) -> int:  # noqa: ARG002 — always raises
        raise RuntimeError("boom")


def _make_job(handler: str = "clarify_sweep", *, params: dict[str, Any] | None = None) -> Job:
    return Job(
        job_id=f"{handler}-{uuid.uuid4().hex[:6]}",
        handler_name=handler,
        schedule="every 30m",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
        params=params or {},
    )


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    """Isolate the process-level HandlerRegistry between tests."""
    HandlerRegistry.reset()
    yield
    HandlerRegistry.reset()


def test_handler_name_is_clarify_sweep() -> None:
    handler = ClarifySweepHandler(ClarifyGateway())
    assert handler.handler_name == "clarify_sweep"


@pytest.mark.asyncio
async def test_execute_drops_stale_entries_and_reports_count() -> None:
    clock = _FakeClock()
    gw = ClarifyGateway(time_fn=clock)

    # Two entries created at t=100; one more at t=150.
    clock.now = 100.0
    await gw.ask("s1", "cli", "old a?")
    await gw.ask("s2", "cli", "old b?")
    clock.now = 150.0
    await gw.ask("s3", "cli", "fresh?")

    # TTL=30s; at t=200 the two t=100 entries (age 100) expire, the t=150 one
    # (age 50) also expires — so use a tighter horizon to keep one fresh.
    clock.now = 160.0  # ages: 60, 60, 10
    handler = ClarifySweepHandler(gw, ttl_seconds=30.0)

    result = await handler.execute(_make_job())

    assert isinstance(result, JobResult)
    assert result.success is True
    assert result.metadata["dropped"] == 2
    assert result.output == "dropped=2"
    # The fresh entry survived; the stale ones are gone.
    assert gw.try_resolve("s3", "cli", "x") is not None
    assert gw.try_resolve("s1", "cli", "x") is None
    assert gw.try_resolve("s2", "cli", "x") is None


@pytest.mark.asyncio
async def test_execute_uses_configured_ttl() -> None:
    clock = _FakeClock()
    gw = ClarifyGateway(time_fn=clock)
    clock.now = 0.0
    await gw.ask("s1", "cli", "q?")

    # At t=1800 with the default 1800s TTL the entry (age 1800 ≥ 1800) expires.
    clock.now = 1800.0
    handler = ClarifySweepHandler(gw)  # default ttl_seconds=1800.0

    result = await handler.execute(_make_job())
    assert result.metadata["dropped"] == 1
    assert result.metadata["ttl_seconds"] == 1800.0


@pytest.mark.asyncio
async def test_execute_never_raises_when_gateway_misbehaves() -> None:
    handler = ClarifySweepHandler(_ExplodingGateway(), ttl_seconds=30.0)  # type: ignore[arg-type]

    result = await handler.execute(_make_job())

    # Self-healing: the exploding sweep is swallowed and reported as 0 dropped.
    assert result.success is True
    assert result.metadata["dropped"] == 0
    assert result.output == "dropped=0"


def test_register_puts_handler_on_registry() -> None:
    gw = ClarifyGateway()
    handler = register_clarify_sweep_handler(gw, ttl_seconds=42.0)

    assert isinstance(handler, ClarifySweepHandler)
    assert handler.handler_name == "clarify_sweep"
    # Registered under its handler_name on the process registry.
    registered = HandlerRegistry.instance().get("clarify_sweep")
    assert registered is handler
