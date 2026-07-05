"""Tests for owl_schedule_guards validation helpers."""

from __future__ import annotations

from stackowl.owls.owl_schedule_guards import schedule_interval_seconds


def test_schedule_interval_seconds_rejects_malformed_daily_body():
    # A stray suffix (e.g. an accidentally-typed timezone abbreviation) after
    # the HH:MM body must not be silently accepted as a valid daily schedule.
    assert schedule_interval_seconds("daily@09:30 CDT") is None


def test_schedule_interval_seconds_accepts_clean_daily():
    assert schedule_interval_seconds("daily@09:00") == 86400.0


def test_schedule_interval_seconds_rejects_out_of_range_hour():
    assert schedule_interval_seconds("daily@24:00") is None
