"""Tests for TelegramQuietHoursConfig and QuietHoursChecker."""

from __future__ import annotations

import pytest

from stackowl.channels.telegram.quiet_hours import (
    QuietHoursChecker,
    TelegramQuietHoursConfig,
)


# ---------------------------------------------------------------------------
# 1. QuietHoursChecker.is_quiet_now returns False when disabled
# ---------------------------------------------------------------------------


def test_is_quiet_now_returns_false_when_disabled() -> None:
    config = TelegramQuietHoursConfig(enabled=False, start_hour=22, end_hour=7)
    checker = QuietHoursChecker(config)
    # Even if current hour would be in the window, disabled means not quiet
    assert checker.is_quiet_now(clock_hour=23) is False


# ---------------------------------------------------------------------------
# 2. is_quiet_now returns True when within start-end range (same day)
# ---------------------------------------------------------------------------


def test_is_quiet_now_true_within_same_day_range() -> None:
    # start=10, end=18 — hour=14 is inside
    config = TelegramQuietHoursConfig(enabled=True, start_hour=10, end_hour=18)
    checker = QuietHoursChecker(config)
    assert checker.is_quiet_now(clock_hour=14) is True


# ---------------------------------------------------------------------------
# 3. is_quiet_now returns False when outside start-end range
# ---------------------------------------------------------------------------


def test_is_quiet_now_false_outside_same_day_range() -> None:
    # start=10, end=18 — hour=20 is outside
    config = TelegramQuietHoursConfig(enabled=True, start_hour=10, end_hour=18)
    checker = QuietHoursChecker(config)
    assert checker.is_quiet_now(clock_hour=20) is False


# ---------------------------------------------------------------------------
# 4. Midnight-spanning range: 23:00 is quiet (start=22, end=7)
# ---------------------------------------------------------------------------


def test_midnight_spanning_range_late_night_is_quiet() -> None:
    config = TelegramQuietHoursConfig(enabled=True, start_hour=22, end_hour=7)
    checker = QuietHoursChecker(config)
    # 23:00 >= 22 → quiet
    assert checker.is_quiet_now(clock_hour=23) is True


# ---------------------------------------------------------------------------
# 5. Midnight-spanning range: 06:00 is quiet (start=22, end=7)
# ---------------------------------------------------------------------------


def test_midnight_spanning_range_early_morning_is_quiet() -> None:
    config = TelegramQuietHoursConfig(enabled=True, start_hour=22, end_hour=7)
    checker = QuietHoursChecker(config)
    # 06:00 < 7 → quiet
    assert checker.is_quiet_now(clock_hour=6) is True


# ---------------------------------------------------------------------------
# 6. Midnight-spanning range: 10:00 is NOT quiet (start=22, end=7)
# ---------------------------------------------------------------------------


def test_midnight_spanning_range_daytime_is_not_quiet() -> None:
    config = TelegramQuietHoursConfig(enabled=True, start_hour=22, end_hour=7)
    checker = QuietHoursChecker(config)
    # 10:00 is neither >= 22 nor < 7 → not quiet
    assert checker.is_quiet_now(clock_hour=10) is False


# ---------------------------------------------------------------------------
# 7. should_suppress returns False for critical when urgent_override=True
# ---------------------------------------------------------------------------


def test_should_suppress_false_for_critical_with_override() -> None:
    config = TelegramQuietHoursConfig(
        enabled=True, start_hour=22, end_hour=7, urgent_override=True
    )
    checker = QuietHoursChecker(config)
    # Even though it's a "quiet" hour, critical with override → do not suppress
    result = checker.should_suppress("critical")
    assert result is False


# ---------------------------------------------------------------------------
# 8. should_suppress returns True during quiet hours for normal urgency
# ---------------------------------------------------------------------------


def test_should_suppress_true_during_quiet_hours_normal_urgency() -> None:
    config = TelegramQuietHoursConfig(
        enabled=True, start_hour=22, end_hour=7, urgent_override=True
    )
    checker = QuietHoursChecker(config)
    # Inject clock_hour=23 by calling is_quiet_now directly to confirm setup,
    # then verify should_suppress delegates correctly via is_quiet_now.
    # We patch is_quiet_now to return True for this test.
    original_is_quiet_now = checker.is_quiet_now
    checker.is_quiet_now = lambda clock_hour=None: True  # type: ignore[method-assign]
    assert checker.should_suppress("normal") is True
    checker.is_quiet_now = original_is_quiet_now  # restore


# ---------------------------------------------------------------------------
# 9. TelegramQuietHoursConfig is frozen (immutable)
# ---------------------------------------------------------------------------


def test_quiet_hours_config_is_frozen() -> None:
    config = TelegramQuietHoursConfig(enabled=True, start_hour=22, end_hour=7)
    with pytest.raises(Exception):
        config.enabled = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 10. TelegramQuietHoursConfig has sensible defaults (enabled=False)
# ---------------------------------------------------------------------------


def test_quiet_hours_config_defaults() -> None:
    config = TelegramQuietHoursConfig()
    assert config.enabled is False
    assert config.start_hour == 22
    assert config.end_hour == 7
    assert config.timezone == "UTC"
    assert config.urgent_override is True
