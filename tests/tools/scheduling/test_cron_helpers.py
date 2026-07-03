"""Tests for cron_helpers — is_valid_schedule and render_recurrence."""

from __future__ import annotations

from stackowl.tools.scheduling.cron_helpers import is_valid_schedule, render_recurrence


def test_is_valid_schedule_accepts_at_token():
    assert is_valid_schedule("at 17:00") is True
    assert is_valid_schedule("at 24:00") is False
    assert is_valid_schedule("at 5:9") is False  # minute must be 2 digits


def test_render_recurrence_at_token_reads_as_once():
    assert render_recurrence("at 17:00") == "once, at 17:00"
