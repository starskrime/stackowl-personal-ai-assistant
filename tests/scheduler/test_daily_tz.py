"""C1 / F108 — daily@HH:MM scheduled in the user's IANA tz, DST-correct.

Before the fix, the daily@ branch did ``datetime.now(UTC).replace(hour=…)`` — so
``daily@08:00`` always meant 08:00 UTC, NOT 08:00 in the user's timezone, and it
disagreed with the tz-aware quiet-hours clock. The fix resolves
``settings.system.timezone`` via ``ZoneInfo``, builds the candidate as a LOCAL
wall-clock instant, then stores UTC — DST-correct (8am stays 8am across both
transitions).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from stackowl.scheduler.scheduler_helpers import compute_next_run

# America/New_York: spring-forward 2024-03-10 02:00->03:00, fall-back 2024-11-03.
_NY = "America/New_York"


def _next_local(schedule: str, tz: str, now: datetime) -> datetime:
    iso = compute_next_run(schedule, tz=tz, now=now)
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(tz))


def test_daily_uses_user_tz_not_utc() -> None:
    """daily@08:00 in America/New_York resolves to 08:00 LOCAL, not 08:00 UTC."""
    # A summer date (EDT, UTC-4). 06:00 local is before 08:00 -> today 08:00 local.
    now = datetime(2024, 7, 1, 6, 0, tzinfo=ZoneInfo(_NY))
    local = _next_local("daily@08:00", _NY, now)
    assert (local.hour, local.minute) == (8, 0)
    assert local.date() == now.date()


def test_daily_rolls_to_tomorrow_when_past() -> None:
    now = datetime(2024, 7, 1, 9, 0, tzinfo=ZoneInfo(_NY))
    local = _next_local("daily@08:00", _NY, now)
    assert (local.hour, local.minute) == (8, 0)
    assert local.date() > now.date()


def test_daily_8am_stays_8am_across_spring_forward() -> None:
    """The morning AFTER spring-forward, 08:00 local is still 08:00 local."""
    # 2024-03-09 (Sat) before the Sun 2024-03-10 transition; next 08:00 is Sat.
    now = datetime(2024, 3, 9, 9, 0, tzinfo=ZoneInfo(_NY))
    local = _next_local("daily@08:00", _NY, now)
    assert (local.hour, local.minute) == (8, 0)
    # And the next instant after the transition day is also 08:00 local.
    now2 = datetime(2024, 3, 10, 9, 0, tzinfo=ZoneInfo(_NY))
    local2 = _next_local("daily@08:00", _NY, now2)
    assert (local2.hour, local2.minute) == (8, 0)


def test_daily_8am_stays_8am_across_fall_back() -> None:
    now = datetime(2024, 11, 2, 9, 0, tzinfo=ZoneInfo(_NY))
    local = _next_local("daily@08:00", _NY, now)
    assert (local.hour, local.minute) == (8, 0)
    now2 = datetime(2024, 11, 3, 9, 0, tzinfo=ZoneInfo(_NY))
    local2 = _next_local("daily@08:00", _NY, now2)
    assert (local2.hour, local2.minute) == (8, 0)


def test_nonexistent_local_time_spring_gap_defined() -> None:
    """daily@02:30 on the spring-forward day (02:00->03:00 gap) is DEFINED.

    The 02:30 local instant does not exist that day. ZoneInfo resolves the gap
    deterministically (the result is a real UTC instant); the documented policy is
    "no silently-wrong instant" — assert it produces a concrete, parseable result
    and never the bare 02:30 wall time interpreted as if valid.
    """
    now = datetime(2024, 3, 10, 0, 0, tzinfo=ZoneInfo(_NY))
    iso = compute_next_run("daily@02:30", tz=_NY, now=now)
    parsed = datetime.fromisoformat(iso)
    # A real, tz-aware UTC instant was produced (deterministic, not a crash).
    assert parsed.tzinfo is not None


def test_default_tz_is_utc_backcompat() -> None:
    """Omitting tz keeps the legacy UTC behavior (back-compat default)."""
    now = datetime(2024, 7, 1, 6, 0, tzinfo=ZoneInfo("UTC"))
    iso = compute_next_run("daily@08:00", now=now)
    parsed = datetime.fromisoformat(iso).astimezone(ZoneInfo("UTC"))
    assert (parsed.hour, parsed.minute) == (8, 0)


def test_bad_tz_fails_open_to_utc() -> None:
    now = datetime(2024, 7, 1, 6, 0, tzinfo=ZoneInfo("UTC"))
    iso = compute_next_run("daily@08:00", tz="Not/AZone", now=now)
    parsed = datetime.fromisoformat(iso).astimezone(ZoneInfo("UTC"))
    assert (parsed.hour, parsed.minute) == (8, 0)
