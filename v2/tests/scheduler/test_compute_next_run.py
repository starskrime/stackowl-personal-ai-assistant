"""Regression tests for ``compute_next_run`` — FIX 2 (every Nm/Nh re-arm bug).

Before the fix, an ``every 5m`` schedule was not valid cron, so ``compute_next_run``
fell into the croniter ``except`` and returned ``now + 1 DAY``. Seeded system jobs
(digest ``every 5m``, sweeps ``every 10m`` …) fired once via their seeded first
run, then silently re-armed to +1d — and the cronjob tool's advertised "~288x/day"
cadence was a lie. The ``every`` branch now parses the fixed-interval DSL token.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from stackowl.scheduler.scheduler_helpers import compute_next_run, parse_every


def _delta_seconds(schedule: str) -> float:
    before = datetime.now(UTC)
    nxt = datetime.fromisoformat(compute_next_run(schedule))
    return (nxt - before).total_seconds()


@pytest.mark.parametrize(
    ("schedule", "expected_seconds"),
    [
        ("every 5m", 300),
        ("every 2h", 7200),
        ("every 30s", 30),
        ("every 1d", 86400),
        ("EVERY 10M", 600),  # case-insensitive
        ("every 15 m", 900),  # optional space
    ],
)
def test_every_schedules_arm_to_correct_interval(schedule: str, expected_seconds: int) -> None:
    delta = _delta_seconds(schedule)
    # Generous tolerance for clock drift between the two now() reads.
    assert abs(delta - expected_seconds) < 5, (
        f"{schedule!r} armed to +{delta}s, expected ~+{expected_seconds}s"
    )


def test_every_5m_is_not_plus_one_day() -> None:
    """The exact bug: ``every 5m`` must NOT re-arm to +1 day."""
    delta = _delta_seconds("every 5m")
    assert delta < 3600, f"every 5m armed to +{delta}s — the +1d re-arm bug is back"


@pytest.mark.parametrize("schedule", ["every 0m", "every -1m", "garbage", "every 5x"])
def test_unparseable_schedule_falls_back_to_plus_one_day(schedule: str) -> None:
    """Genuinely-unparseable input keeps the graceful +1d fallback (no raise)."""
    delta = _delta_seconds(schedule)
    assert abs(delta - 86400) < 5, f"{schedule!r} should fall back to +1d, got +{delta}s"


def test_daily_and_cron_still_work() -> None:
    # daily@ still parses (next slot today or tomorrow).
    assert "T" in compute_next_run("daily@09:30")
    # 5-field cron still parses via croniter.
    delta = _delta_seconds("*/5 * * * *")
    assert 0 < delta <= 300


@pytest.mark.parametrize(
    ("schedule", "ok"),
    [("every 5m", True), ("every 30s", True), ("every 1d", True), ("every 0m", False), ("nope", False)],
)
def test_parse_every_agrees(schedule: str, ok: bool) -> None:
    assert (parse_every(schedule) is not None) is ok
