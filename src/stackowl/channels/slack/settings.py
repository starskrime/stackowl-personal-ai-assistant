"""SlackSettings — typed configuration for the Slack channel adapter."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SlackSettings(BaseModel):
    """Slack channel configuration.

    Attributes:
        bot_token: Slack bot token (sensitive — never log raw value).
        signing_secret: Slack signing secret used to verify request payloads
            (sensitive — never log raw value).
        allowed_user_ids: Allowlist of Slack user IDs permitted to message the
            bot. An empty list means "no users are allowed" — every event is
            silently dropped (fail-closed).
        socket_mode: When ``True`` the adapter would use Socket Mode (no public
            webhook required); when ``False`` it expects to receive events via
            an HTTP endpoint. The adapter itself does not open the connection —
            the production runner is responsible for that.
        app_token: Slack app-level token (``xapp-…``) carrying the
            ``connections:write`` scope. Required by Socket Mode and SEPARATE
            from the bot token (``xoxb-…``); the production runner uses it to
            open the WebSocket. Sensitive — never log the raw value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bot_token: str = Field(default="", json_schema_extra={"sensitive": True})
    signing_secret: str = Field(default="", json_schema_extra={"sensitive": True})
    allowed_user_ids: list[str] = Field(default_factory=list)
    socket_mode: bool = True
    app_token: str = Field(default="", json_schema_extra={"sensitive": True})
