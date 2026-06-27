"""Notification-related settings sub-models (Story 7.4).

Kept in a dedicated module so :mod:`stackowl.config.settings` stays under
the B2 300-line cap. Re-exported from :mod:`stackowl.config.settings`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class QuietHoursSettings(BaseModel):
    """Quiet-hours window during which non-critical notifications are batched."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Master toggle for quiet hours.",
        json_schema_extra={"hot_reload": True},
    )
    start: str = Field(
        default="22:00",
        description="Local-time HH:MM at which quiet hours begin.",
        json_schema_extra={"hot_reload": True},
    )
    end: str = Field(
        default="08:00",
        description="Local-time HH:MM at which quiet hours end.",
        json_schema_extra={"hot_reload": True},
    )
    timezone: str = Field(
        default="UTC",
        description="IANA timezone used to evaluate the start/end window.",
        json_schema_extra={"hot_reload": True},
    )


class NotificationSettings(BaseModel):
    """Notification routing and delivery configuration (Story 7.4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    default_channel: str = Field(
        default="cli",
        description="Channel used when a notification omits ``channel_name``.",
        json_schema_extra={"hot_reload": True},
    )
    fallback_channel: str = Field(
        default="",
        description=(
            "ADR-2 — when transport to the target channel FAILS (after the in-channel "
            "retry), the RecoveryActuator reroutes the message to THIS channel before "
            "surrendering. Empty (the default) ⇒ no reroute (byte-identical: a failed "
            "transport stays 'failed'). Set to a registered channel name to opt in. A "
            "reroute to the same channel that just failed is skipped."
        ),
        json_schema_extra={"hot_reload": True},
    )
    quiet_hours: QuietHoursSettings = Field(default_factory=QuietHoursSettings)
    subscriptions: dict[str, bool] = Field(
        default_factory=dict,
        description="Per-category opt-in map keyed by category name.",
        json_schema_extra={"hot_reload": True},
    )
    max_notifications_per_hour: int = Field(
        default=10,
        ge=0,
        description="Rate-limit cap enforced by the notification router.",
        json_schema_extra={"hot_reload": True},
    )
