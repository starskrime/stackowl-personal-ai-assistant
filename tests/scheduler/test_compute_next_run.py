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

from stackowl.scheduler.scheduler_helpers import compute_next_run, parse_every, parse_in


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


# --------------------------------------------------------------------------- REMINDER-FIX
# ``parse_in`` — one-shot relative-delay token ("remind me in 5 minutes" bug fix).


@pytest.mark.parametrize(
    ("schedule", "expected_seconds"),
    [
        ("in 5m", 300),
        ("in 2 hours", 7200),
        ("in 30s", 30),
        ("in 1d", 86400),
        ("IN 10M", 600),  # case-insensitive
        ("in 15 min", 900),
        ("in 1 hour", 3600),
        ("in 2 days", 172800),
    ],
)
def test_parse_in_unit_table(schedule: str, expected_seconds: int) -> None:
    delta = parse_in(schedule)
    assert delta is not None
    assert delta.total_seconds() == expected_seconds


@pytest.mark.parametrize(
    "schedule",
    ["every 5m", "in 0m", "in -1m", "daily@09:00", "0 9 * * *", "garbage", "increment 5m"],
)
def test_parse_in_rejects_non_one_shot_tokens(schedule: str) -> None:
    assert parse_in(schedule) is None


def test_compute_next_run_in_5m_is_one_shot_not_recurring() -> None:
    """The exact bug fix: 'in 5m' must arm ~5m out, not recur or fall back to +1d."""
    delta = _delta_seconds("in 5m")
    assert abs(delta - 300) < 5, f"'in 5m' armed to +{delta}s, expected ~+300s"
