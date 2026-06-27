"""F-85 support — HealthAggregator.is_live gates the systemd watchdog ping."""

from __future__ import annotations

import pytest

from stackowl.health.aggregator import HealthAggregator
from stackowl.health.status import HealthStatus


class _Contributor:
    def __init__(self, name: str, status: str) -> None:
        self._name = name
        self._status = status

    @property
    def contributor_name(self) -> str:
        return self._name

    async def health_check(self) -> HealthStatus:
        return HealthStatus(self._name, self._status, None, 1.0)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_is_live_true_with_no_contributors() -> None:
    # Fail-open: nothing registered → considered live (safe to wire pre-contributors).
    assert await HealthAggregator().is_live() is True


@pytest.mark.asyncio
async def test_is_live_true_when_all_ok() -> None:
    agg = HealthAggregator()
    agg.register(_Contributor("db", "ok"))
    agg.register(_Contributor("fs", "ok"))
    assert await agg.is_live() is True


@pytest.mark.asyncio
async def test_is_live_true_when_only_degraded() -> None:
    # Degraded subsystems are still serving — do NOT trip a watchdog restart.
    agg = HealthAggregator()
    agg.register(_Contributor("graph", "degraded"))
    assert await agg.is_live() is True


@pytest.mark.asyncio
async def test_is_live_false_when_a_subsystem_down() -> None:
    agg = HealthAggregator()
    agg.register(_Contributor("db", "down"))
    agg.register(_Contributor("fs", "ok"))
    assert await agg.is_live() is False
