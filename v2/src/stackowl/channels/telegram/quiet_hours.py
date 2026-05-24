"""TelegramQuietHoursConfig and QuietHoursChecker — Telegram-specific quiet hours.

Quiet hours suppress proactive notifications during configurable time windows.
Critical notifications can bypass quiet hours via ``urgent_override``.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict

from stackowl.infra.observability import log


class TelegramQuietHoursConfig(BaseModel):
    """Telegram-specific quiet hours configuration.

    Attributes:
        enabled: Whether quiet hours enforcement is active.
        start_hour: Hour (0-23) when quiet period begins (local time).
        end_hour: Hour (0-23) when quiet period ends (local time).
            If ``start_hour > end_hour`` the window wraps across midnight.
        timezone: IANA timezone name used to interpret ``start_hour``/``end_hour``.
        urgent_override: When ``True`` critical notifications bypass quiet hours.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    start_hour: int = 22  # 0-23 in local time
    end_hour: int = 7  # 0-23 in local time; if start > end, wraps midnight
    timezone: str = "UTC"
    urgent_override: bool = True  # critical notifications bypass quiet hours


class QuietHoursChecker:
    """Determines whether the current time falls inside the configured quiet window."""

    def __init__(self, config: TelegramQuietHoursConfig) -> None:
        self._config = config
        log.telegram.debug(
            "[telegram] quiet_hours.init: entry",
            extra={
                "_fields": {
                    "enabled": config.enabled,
                    "start_hour": config.start_hour,
                    "end_hour": config.end_hour,
                    "timezone": config.timezone,
                }
            },
        )

    def is_quiet_now(self, clock_hour: int | None = None) -> bool:
        """Return ``True`` if the current time is inside the quiet window.

        4-point logging: entry / decision / step / exit.

        Args:
            clock_hour: Override for the current hour (0-23). When ``None``
                the real wall-clock hour in ``config.timezone`` is used.
                Intended for deterministic testing.

        Returns:
            ``False`` when quiet hours are disabled; otherwise ``True`` when
            the current local hour falls inside [start_hour, end_hour).
        """
        log.telegram.debug(
            "[telegram] quiet_hours.is_quiet_now: entry",
            extra={"_fields": {"enabled": self._config.enabled, "clock_hour_override": clock_hour}},
        )

        if not self._config.enabled:
            log.telegram.debug("[telegram] quiet_hours.is_quiet_now: decision disabled — skip")
            log.telegram.debug(
                "[telegram] quiet_hours.is_quiet_now: exit",
                extra={"_fields": {"quiet": False}},
            )
            return False

        if clock_hour is None:
            try:
                tz = ZoneInfo(self._config.timezone)
                hour = datetime.now(tz).hour
            except ZoneInfoNotFoundError:
                log.telegram.warning(
                    "[telegram] quiet_hours.is_quiet_now: unknown timezone — defaulting UTC",
                    extra={"_fields": {"timezone": self._config.timezone}},
                )
                hour = datetime.now(ZoneInfo("UTC")).hour
        else:
            hour = clock_hour

        start = self._config.start_hour
        end = self._config.end_hour

        log.telegram.debug(
            "[telegram] quiet_hours.is_quiet_now: step hour_resolved",
            extra={"_fields": {"hour": hour, "start": start, "end": end}},
        )

        if start <= end:
            # Simple same-day window: quiet if start <= hour < end
            quiet = start <= hour < end
        else:
            # Midnight-spanning window: quiet if hour >= start OR hour < end
            quiet = hour >= start or hour < end

        log.telegram.debug(
            "[telegram] quiet_hours.is_quiet_now: exit",
            extra={"_fields": {"quiet": quiet, "hour": hour}},
        )
        return quiet

    def should_suppress(self, urgency: str) -> bool:
        """Return ``True`` if the notification should be suppressed right now.

        4-point logging: entry / decision / step / exit.

        Args:
            urgency: One of ``"low"``, ``"normal"``, or ``"critical"``.

        Returns:
            ``False`` for critical notifications when ``urgent_override`` is set;
            otherwise delegates to :meth:`is_quiet_now`.
        """
        log.telegram.debug(
            "[telegram] quiet_hours.should_suppress: entry",
            extra={"_fields": {"urgency": urgency}},
        )

        if urgency == "critical" and self._config.urgent_override:
            log.telegram.debug(
                "[telegram] quiet_hours.should_suppress: decision critical_override — allow",
            )
            log.telegram.debug(
                "[telegram] quiet_hours.should_suppress: exit",
                extra={"_fields": {"suppress": False, "reason": "critical_override"}},
            )
            return False

        quiet = self.is_quiet_now()
        log.telegram.debug(
            "[telegram] quiet_hours.should_suppress: step quiet_check",
            extra={"_fields": {"quiet": quiet}},
        )
        log.telegram.debug(
            "[telegram] quiet_hours.should_suppress: exit",
            extra={"_fields": {"suppress": quiet, "urgency": urgency}},
        )
        return quiet
