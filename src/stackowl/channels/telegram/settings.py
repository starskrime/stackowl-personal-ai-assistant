"""TelegramSettings — typed configuration for the Telegram channel adapter."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from stackowl.channels.telegram.quiet_hours import TelegramQuietHoursConfig


class TelegramSettings(BaseModel):
    """Telegram channel configuration.

    Attributes:
        bot_token: Bot API token (sensitive — never log raw value).
        allowed_user_ids: Allowlist of Telegram user IDs permitted to message
            the bot. An empty frozenset means "no users are allowed" — every
            message is silently dropped (fail-closed).
        webhook_url: When set, the adapter registers a webhook with Telegram
            and receives updates via HTTP POST. When ``None``, polling is used.
        webhook_secret: Optional HTTPS secret token sent by Telegram in the
            ``X-Telegram-Bot-Api-Secret-Token`` header for webhook mode.
        socket_mode: Legacy compatibility field; unused by Telegram.
        suppress_evolution_events: When ``True`` the adapter does not surface
            owl evolution notifications to the Telegram channel.
        quiet_hours: Telegram-specific quiet hours configuration; overrides
            any global quiet-hours setting for this channel.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bot_token: str = Field(default="", json_schema_extra={"sensitive": True})
    allowed_user_ids: frozenset[int] = Field(default_factory=frozenset)
    webhook_url: str | None = None
    webhook_secret: str = Field(default="", json_schema_extra={"sensitive": True})
    socket_mode: bool = False
    suppress_evolution_events: bool = False
    quiet_hours: TelegramQuietHoursConfig = Field(
        default_factory=TelegramQuietHoursConfig
    )
