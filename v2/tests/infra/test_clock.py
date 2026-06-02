"""Clock.now() — wall-clock time source for time-based components.

Verifies:
- WallClock().now() returns a timezone-aware UTC datetime.
- A FixedClock test double returns the fixed instant from now().
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from stackowl.infra.clock import Clock, WallClock


class FixedClock:
    """Deterministic Clock test double — now() returns a frozen instant."""

    def __init__(self, instant: datetime) -> None:
        self._instant = instant

    def now(self) -> datetime:
        return self._instant

    def monotonic(self) -> float:
        return 0.0

    async def async_sleep(self, seconds: float) -> None:  # pragma: no cover
        return None


def test_wallclock_now_is_tz_aware_utc() -> None:
    now = WallClock().now()
    assert now.tzinfo is not None
    # Offset is UTC (zero).
    assert now.utcoffset() == timedelta(0)


def test_wallclock_now_is_recent() -> None:
    before = datetime.now(UTC)
    now = WallClock().now()
    after = datetime.now(UTC)
    assert before <= now <= after


def test_fixed_clock_returns_fixed_instant() -> None:
    instant = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FixedClock(instant)
    assert clock.now() == instant


def test_wallclock_satisfies_clock_protocol() -> None:
    assert isinstance(WallClock(), Clock)
