"""DiscordSettings — typed configuration for the Discord channel adapter."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DiscordSettings(BaseModel):
    """Discord channel configuration.

    Attributes:
        bot_token: Discord bot token (sensitive — never log raw value).
        allowed_user_ids: Allowlist of Discord user IDs permitted to message
            the bot. An empty list means "no users are allowed" — every
            message is silently dropped (fail-closed).
        guild_id: Optional guild scope. When ``None`` the bot accepts DMs and
            any guild it is a member of.
        socket_mode: Reserved for parity with the Slack adapter; ignored by
            discord.py which always uses a WebSocket gateway.
        suppress_evolution_events: When ``True`` the adapter does not surface
            owl evolution notifications to Discord channels.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bot_token: str = Field(default="", json_schema_extra={"sensitive": True})
    allowed_user_ids: list[int] = Field(default_factory=list)
    guild_id: int | None = None
    socket_mode: bool = True
    suppress_evolution_events: bool = False
    # Gate for orchestrator startup wiring (F004-part2). Defaults False so the
    # channel is never accidentally started before its send path + consent
    # prompter are wired — documents the gap rather than hiding it.
    enabled: bool = False
