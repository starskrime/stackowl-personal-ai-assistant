"""F-87 — HealthSweepHandler periodic detect + operator alert."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from stackowl.health.status import HealthStatus
from stackowl.scheduler.handlers.health_sweep import HealthSweepHandler
from stackowl.scheduler.job import Job


class _FakeAggregator:
    def __init__(self, statuses: list[HealthStatus]) -> None:
        self._statuses = statuses

    async def collect(self) -> list[HealthStatus]:
        return self._statuses

    def set(self, statuses: list[HealthStatus]) -> None:
        self._statuses = statuses


class _FakeClock:
    """Controllable clock for dedup/backoff tests — advance() moves monotonic()."""

    def __init__(self) -> None:
        self._t = 1000.0

    def monotonic(self) -> float:
        return self._t

    def now(self) -> datetime:  # pragma: no cover — unused by health_sweep
        return datetime.now(UTC)

    async def async_sleep(self, seconds: float) -> None:  # pragma: no cover — unused
        return None

    def advance(self, seconds: float) -> None:
        self._t += seconds


def _async_sink(alerts: list[str]):  # noqa: ANN201
    """Async alert-sink recorder — ``AlertSink`` is ``Callable[[str], Awaitable[None]]``."""

    async def _sink(msg: str) -> None:
        alerts.append(msg)

    return _sink


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


# --------------------------------------------------------------------------
# Alert dedup / backoff (one ongoing incident = one alert, not one per tick)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_degraded_sweep_alerts_once() -> None:
    agg = _FakeAggregator([HealthStatus("graph", "degraded", "x", 2.0)])
    alerts: list[str] = []
    clock = _FakeClock()

    handler = HealthSweepHandler(
        agg, alert=_async_sink(alerts), clock=clock, realert_backoff_s=3600.0  # type: ignore[arg-type]
    )
    result = await handler.execute(_job())

    assert result.success is True
    assert len(alerts) == 1
    assert "graph" in alerts[0]


@pytest.mark.asyncio
async def test_immediate_repeat_same_status_does_not_realert() -> None:
    agg = _FakeAggregator([HealthStatus("graph", "degraded", "x", 2.0)])
    alerts: list[str] = []
    clock = _FakeClock()

    handler = HealthSweepHandler(
        agg, alert=_async_sink(alerts), clock=clock, realert_backoff_s=3600.0  # type: ignore[arg-type]
    )
    await handler.execute(_job())
    await handler.execute(_job())

    assert len(alerts) == 1  # second tick suppressed — same ongoing incident


@pytest.mark.asyncio
async def test_backoff_elapsed_heartbeat_realerts() -> None:
    agg = _FakeAggregator([HealthStatus("graph", "degraded", "x", 2.0)])
    alerts: list[str] = []
    clock = _FakeClock()

    handler = HealthSweepHandler(
        agg, alert=_async_sink(alerts), clock=clock, realert_backoff_s=3600.0  # type: ignore[arg-type]
    )
    await handler.execute(_job())
    clock.advance(3600.0)
    await handler.execute(_job())

    assert len(alerts) == 2  # heartbeat re-alert once backoff has elapsed


@pytest.mark.asyncio
async def test_escalation_degraded_to_down_bypasses_backoff() -> None:
    agg = _FakeAggregator([HealthStatus("graph", "degraded", "x", 2.0)])
    alerts: list[str] = []
    clock = _FakeClock()

    handler = HealthSweepHandler(
        agg, alert=_async_sink(alerts), clock=clock, realert_backoff_s=3600.0  # type: ignore[arg-type]
    )
    await handler.execute(_job())
    agg.set([HealthStatus("graph", "down", "worse now", 2.0)])
    await handler.execute(_job())  # well within backoff, but status LEVEL changed

    assert len(alerts) == 2
    assert "down" in alerts[1]


@pytest.mark.asyncio
async def test_recovery_fires_resolved_notice_and_clears_state() -> None:
    agg = _FakeAggregator([HealthStatus("graph", "degraded", "x", 2.0)])
    alerts: list[str] = []
    clock = _FakeClock()

    handler = HealthSweepHandler(
        agg, alert=_async_sink(alerts), clock=clock, realert_backoff_s=3600.0  # type: ignore[arg-type]
    )
    await handler.execute(_job())
    assert handler._alert_state  # populated after the first unhealthy sweep

    agg.set([HealthStatus("graph", "ok", None, 1.0)])
    await handler.execute(_job())

    assert len(alerts) == 2
    assert "recovered" in alerts[1]
    assert "graph" in alerts[1]
    assert handler._alert_state == {}  # cleared for the recovered subsystem


@pytest.mark.asyncio
async def test_fully_healthy_sweep_with_nothing_tracked_has_no_resolved_notice() -> None:
    agg = _FakeAggregator([HealthStatus("db", "ok", None, 1.0)])
    alerts: list[str] = []
    clock = _FakeClock()

    handler = HealthSweepHandler(agg, alert=_async_sink(alerts), clock=clock)  # type: ignore[arg-type]
    await handler.execute(_job())

    assert alerts == []  # nothing was ever tracked — no spurious resolved-notice


@pytest.mark.asyncio
async def test_new_incident_not_swallowed_by_unrelated_ongoing_dedup() -> None:
    """svc_alpha already deduped/suppressed + svc_beta newly degraded →
    alert fires with ONLY svc_beta."""
    agg = _FakeAggregator([HealthStatus("svc_alpha", "degraded", "x", 1.0)])
    alerts: list[str] = []
    clock = _FakeClock()

    handler = HealthSweepHandler(
        agg, alert=_async_sink(alerts), clock=clock, realert_backoff_s=3600.0  # type: ignore[arg-type]
    )
    await handler.execute(_job())  # svc_alpha alerts + is tracked
    assert len(alerts) == 1

    agg.set(
        [
            HealthStatus("svc_alpha", "degraded", "x", 1.0),  # unchanged — within backoff
            HealthStatus("svc_beta", "degraded", "y", 1.0),  # brand new incident
        ]
    )
    await handler.execute(_job())

    assert len(alerts) == 2
    assert "svc_alpha" not in alerts[1]
    assert "svc_beta" in alerts[1]


@pytest.mark.asyncio
async def test_heal_and_verify_path_reaching_full_health_fires_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The post-heal-and-reverify branch (~line 138) also fires a resolved-notice."""
    from types import SimpleNamespace

    import stackowl.config.settings as settings_mod

    monkeypatch.setattr(
        settings_mod, "Settings", lambda: SimpleNamespace(health_loop=True)
    )

    class _ScriptedAggregator:
        """detect: db down -> heal -> verify: db ok."""

        def __init__(self) -> None:
            self._rounds = [
                [HealthStatus("db", "down", "wedged", 5.0)],
                [HealthStatus("db", "ok", None, 1.0)],
            ]
            self._calls = 0

        async def collect(self) -> list[HealthStatus]:
            idx = min(self._calls, len(self._rounds) - 1)
            self._calls += 1
            return self._rounds[idx]

    class _FakeHealable:
        async def ensure_available(self) -> None:
            return None

    agg = _ScriptedAggregator()
    alerts: list[str] = []
    clock = _FakeClock()

    handler = HealthSweepHandler(
        agg,  # type: ignore[arg-type]
        alert=_async_sink(alerts),  # type: ignore[arg-type]
        healers={"db": _FakeHealable()},  # type: ignore[dict-item]
        clock=clock,
    )
    # Seed prior alert state as if an earlier tick already alerted "db down" —
    # this tick's heal-and-reverify recovering it must produce a resolved-notice.
    handler._alert_state["db"] = ("down", clock.monotonic())

    result = await handler.execute(_job())

    assert result.success is True
    assert len(alerts) == 1
    assert "recovered" in alerts[0]
    assert "db" in alerts[0]
    assert handler._alert_state == {}


@pytest.mark.asyncio
async def test_unhealthy_log_fires_every_tick_regardless_of_dedup_suppression(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regression: the UNHEALTHY log must keep firing even when the alert-sink
    send itself is suppressed by dedup (dedup only ever gates the outbound send)."""
    import logging

    agg = _FakeAggregator([HealthStatus("graph", "degraded", "x", 2.0)])
    alerts: list[str] = []
    clock = _FakeClock()

    handler = HealthSweepHandler(
        agg, alert=_async_sink(alerts), clock=clock, realert_backoff_s=3600.0  # type: ignore[arg-type]
    )
    await handler.execute(_job())  # tick 1 — alerts + logs

    with caplog.at_level(logging.ERROR):
        await handler.execute(_job())  # tick 2 — suppressed alert, but MUST still log

    assert len(alerts) == 1  # alert-sink send suppressed on tick 2
    assert any(
        "UNHEALTHY subsystems detected" in rec.message for rec in caplog.records
    )
