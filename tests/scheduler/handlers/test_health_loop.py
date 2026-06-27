"""ADR-6 — HealthSweepHandler closed loop: detect → heal → verify → escalate.

With ``settings.health_loop`` ON, a down/degraded subsystem that has a registered
HealableResource is recycled (``ensure_available``) and the sweep RE-COLLECTS to
verify recovery: a healed subsystem does NOT alert; one still down after the heal
escalates. Flag OFF (or no healer) = the pre-ADR detect+alert path, byte-identical.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import stackowl.config.settings as settings_mod
from stackowl.health.status import HealthStatus
from stackowl.scheduler.handlers.health_sweep import HealthSweepHandler
from stackowl.scheduler.job import Job


class _ScriptedAggregator:
    """Returns the next scripted status list on each ``collect`` (detect, then verify)."""

    def __init__(self, *rounds: list[HealthStatus]) -> None:
        self._rounds = list(rounds)
        self.collects = 0

    async def collect(self) -> list[HealthStatus]:
        idx = min(self.collects, len(self._rounds) - 1)
        self.collects += 1
        return self._rounds[idx]


class _FakeHealable:
    """HealableResource that becomes available after one ``ensure_available``."""

    def __init__(self, *, available: bool) -> None:
        self.available = available
        self.unavailable_reason = None if available else "dead"
        self.ensures = 0
        self._heals = True

    async def ensure_available(self) -> None:
        self.ensures += 1
        if self._heals:
            self.available = True
            self.unavailable_reason = None

    def register_on_recycled(self, cb) -> None:  # noqa: ANN001
        pass


def _job() -> Job:
    return Job(
        job_id="hl-1", handler_name="health_sweep", schedule="every 5m",
        idempotency_key="health_sweep:every-5m", last_run_at=None,
        next_run_at="2026-06-27T00:00:00+00:00", status="pending",
    )


@pytest.fixture
def _flag(monkeypatch):  # noqa: ANN202
    def _set(value: bool) -> None:
        monkeypatch.setattr(
            settings_mod, "Settings", lambda: SimpleNamespace(health_loop=value)
        )
    return _set


@pytest.mark.asyncio
async def test_down_subsystem_healed_and_reverified_no_alert(_flag) -> None:
    _flag(True)
    # detect: db down → heal → verify: db ok.
    agg = _ScriptedAggregator(
        [HealthStatus("db", "down", "pool wedged", 5000.0)],
        [HealthStatus("db", "ok", None, 1.0)],
    )
    healer = _FakeHealable(available=False)
    alerts: list[str] = []

    async def _sink(msg: str) -> None:
        alerts.append(msg)

    handler = HealthSweepHandler(agg, alert=_sink, healers={"db": healer})
    result = await handler.execute(_job())

    assert healer.ensures == 1, "the down resource must be recycled"
    assert agg.collects == 2, "must re-collect to verify the heal"
    assert alerts == [], "a healed subsystem must NOT escalate"
    assert result.success is True
    assert result.metadata["healed"] == 1


@pytest.mark.asyncio
async def test_unhealable_subsystem_still_escalates(_flag) -> None:
    _flag(True)
    # detect: db down → heal attempted → verify: still down → escalate.
    agg = _ScriptedAggregator(
        [HealthStatus("db", "down", "pool wedged", 5000.0)],
        [HealthStatus("db", "down", "pool wedged", 5000.0)],
    )
    healer = _FakeHealable(available=False)
    healer._heals = False  # recycle does not fix it
    alerts: list[str] = []

    async def _sink(msg: str) -> None:
        alerts.append(msg)

    handler = HealthSweepHandler(agg, alert=_sink, healers={"db": healer})
    result = await handler.execute(_job())

    assert healer.ensures == 1
    assert len(alerts) == 1, "a subsystem still down after heal must escalate"
    assert "db" in alerts[0]
    assert result.metadata["down"] == 1


@pytest.mark.asyncio
async def test_flag_off_is_detect_only_byte_identical(_flag) -> None:
    _flag(False)
    agg = _ScriptedAggregator([HealthStatus("db", "down", "pool wedged", 5000.0)])
    healer = _FakeHealable(available=False)
    alerts: list[str] = []

    async def _sink(msg: str) -> None:
        alerts.append(msg)

    handler = HealthSweepHandler(agg, alert=_sink, healers={"db": healer})
    result = await handler.execute(_job())

    assert healer.ensures == 0, "flag OFF must not heal"
    assert agg.collects == 1, "flag OFF must not re-collect"
    assert len(alerts) == 1, "flag OFF is the pre-ADR detect+alert path"
    assert result.metadata["down"] == 1


@pytest.mark.asyncio
async def test_flag_on_no_healer_is_detect_only(_flag) -> None:
    _flag(True)
    # ON but a down subsystem with no registered healer → no heal, alerts as before.
    agg = _ScriptedAggregator([HealthStatus("graph", "down", "kuzu gone", 2.0)])
    alerts: list[str] = []

    async def _sink(msg: str) -> None:
        alerts.append(msg)

    handler = HealthSweepHandler(agg, alert=_sink, healers={})
    result = await handler.execute(_job())

    assert agg.collects == 1, "nothing to heal → no re-collect"
    assert len(alerts) == 1
    assert result.metadata["down"] == 1
