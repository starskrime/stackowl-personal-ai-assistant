"""F-87 — HealthSweepHandler periodic detect + operator alert."""

from __future__ import annotations

import pytest

from stackowl.health.status import HealthStatus
from stackowl.scheduler.handlers.health_sweep import HealthSweepHandler
from stackowl.scheduler.job import Job


class _FakeAggregator:
    def __init__(self, statuses: list[HealthStatus]) -> None:
        self._statuses = statuses

    async def collect(self) -> list[HealthStatus]:
        return self._statuses


def _job() -> Job:
    return Job(
        job_id="hs-1",
        handler_name="health_sweep",
        schedule="every 5m",
        idempotency_key="health_sweep:every-5m",
        last_run_at=None,
        next_run_at="2026-06-26T00:00:00+00:00",
        status="pending",
    )


@pytest.mark.asyncio
async def test_all_healthy_no_alert() -> None:
    agg = _FakeAggregator([HealthStatus("db", "ok", None, 1.0)])
    alerts: list[str] = []

    handler = HealthSweepHandler(agg, alert=alerts.append)  # type: ignore[arg-type]
    result = await handler.execute(_job())

    assert result.success is True
    assert result.metadata["down"] == 0
    assert alerts == []  # quiet when healthy


@pytest.mark.asyncio
async def test_down_subsystem_triggers_alert() -> None:
    agg = _FakeAggregator(
        [
            HealthStatus("db", "down", "pool wedged", 5000.0),
            HealthStatus("filesystem", "ok", None, 1.0),
        ]
    )
    alerts: list[str] = []

    async def _sink(msg: str) -> None:
        alerts.append(msg)

    handler = HealthSweepHandler(agg, alert=_sink)
    result = await handler.execute(_job())

    assert result.success is True  # the sweep itself ran fine; "down" is metadata
    assert result.metadata["down"] == 1
    assert len(alerts) == 1
    assert "db" in alerts[0] and "down" in alerts[0]


@pytest.mark.asyncio
async def test_degraded_alerts_but_handler_succeeds() -> None:
    agg = _FakeAggregator([HealthStatus("graph", "degraded", "kuzu wheel missing", 2.0)])
    alerts: list[str] = []

    async def _sink(msg: str) -> None:
        alerts.append(msg)

    handler = HealthSweepHandler(agg, alert=_sink)
    result = await handler.execute(_job())

    assert result.success is True
    assert result.metadata["degraded"] == 1
    assert len(alerts) == 1


@pytest.mark.asyncio
async def test_no_alert_sink_still_logs_and_succeeds() -> None:
    agg = _FakeAggregator([HealthStatus("db", "down", "x", 1.0)])
    handler = HealthSweepHandler(agg, alert=None)
    result = await handler.execute(_job())
    assert result.success is True
    assert result.metadata["down"] == 1


@pytest.mark.asyncio
async def test_alert_sink_failure_does_not_fail_sweep() -> None:
    agg = _FakeAggregator([HealthStatus("db", "down", "x", 1.0)])

    async def _boom(_msg: str) -> None:
        raise RuntimeError("delivery broke")

    handler = HealthSweepHandler(agg, alert=_boom)
    result = await handler.execute(_job())
    assert result.success is True  # alert failure is swallowed + logged


@pytest.mark.asyncio
async def test_aggregator_raises_returns_failure() -> None:
    class _Boom:
        async def collect(self) -> list[HealthStatus]:
            raise RuntimeError("aggregator broke")

    handler = HealthSweepHandler(_Boom())  # type: ignore[arg-type]
    result = await handler.execute(_job())
    assert result.success is False
    assert "aggregator broke" in (result.error or "")
