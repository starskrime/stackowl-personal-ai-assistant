"""Tests for scheduler_helpers parsing functions — parse_at (REMINDER-FIX-2)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from stackowl.scheduler.scheduler_helpers import compute_next_run, parse_at


def test_parse_at_valid_hhmm():
    assert parse_at("at 17:00") == (17, 0)
    assert parse_at("AT 5:30") == (5, 30)


def test_parse_at_rejects_bad_input():
    assert parse_at("at 24:00") is None
    assert parse_at("at 5:70") is None
    assert parse_at("daily@17:00") is None
    assert parse_at("") is None


def test_compute_next_run_at_today_future_time():
    # now=10:00 local UTC, "at 17:00" should land TODAY at 17:00 UTC.
    now = datetime(2026, 7, 3, 10, 0, tzinfo=UTC)
    next_run = compute_next_run("at 17:00", tz="UTC", now=now)
    assert next_run.startswith("2026-07-03T17:00:00")


def test_compute_next_run_at_past_time_rolls_to_tomorrow():
    # now=18:00 local UTC, "at 17:00" already passed today -> tomorrow.
    now = datetime(2026, 7, 3, 18, 0, tzinfo=UTC)
    next_run = compute_next_run("at 17:00", tz="UTC", now=now)
    assert next_run.startswith("2026-07-04T17:00:00")
