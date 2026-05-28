"""Tests for ResilienceContributor — reports per-subsystem recycle state."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from stackowl.health.contributors import ResilienceContributor

pytestmark = pytest.mark.asyncio


class _StubResource:
    def __init__(
        self,
        *,
        available: bool = True,
        recycle_count: int = 0,
        unavailable_reason: str | None = None,
    ) -> None:
        self.available = available
        self.recycle_count = recycle_count
        self.unavailable_reason = unavailable_reason

    async def ensure_available(self) -> None:
        return

    def register_on_recycled(self, cb: Callable[[], None]) -> None:
        return


async def test_all_healthy_returns_ok() -> None:
    contrib = ResilienceContributor({
        "browser": _StubResource(),
        "db_pool": _StubResource(),
    })
    status = await contrib.health_check()
    assert status.name == "resilience"
    assert status.status == "ok"
    assert "browser:ok" in status.message
    assert "db_pool:ok" in status.message


async def test_recycle_counts_are_reported() -> None:
    contrib = ResilienceContributor({
        "browser": _StubResource(recycle_count=3),
        "db_pool": _StubResource(recycle_count=0),
    })
    status = await contrib.health_check()
    assert status.status == "ok"
    assert "browser:ok(recycles=3)" in status.message
    assert "db_pool:ok" in status.message


async def test_unavailable_subsystem_degrades_status() -> None:
    contrib = ResilienceContributor({
        "browser": _StubResource(),
        "db_pool": _StubResource(available=False, unavailable_reason="disk full"),
    })
    status = await contrib.health_check()
    assert status.status == "degraded"
    assert "db_pool:DOWN(disk full)" in status.message


async def test_no_resources_reports_ok_with_message() -> None:
    contrib = ResilienceContributor({})
    status = await contrib.health_check()
    assert status.status == "ok"
    assert "no healable resources" in status.message


async def test_contributor_name_is_resilience() -> None:
    contrib = ResilienceContributor({})
    assert contrib.contributor_name == "resilience"
