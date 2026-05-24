"""Webhook-related settings sub-models (Story 7.5).

Kept in a dedicated module so :mod:`stackowl.config.settings` stays under
the B2 300-line cap.  Re-exported from :mod:`stackowl.config.settings`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WebhookSourceConfig(BaseModel):
    """A single registered webhook source — secret is resolved via SecretResolver.

    ``secret`` accepts the same syntax as every other secret reference in
    StackOwl: ``env:VAR_NAME``, ``file:/path``, or ``keychain:<service>``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Master toggle for this source — disabled sources reject all requests.",
        json_schema_extra={"hot_reload": True},
    )
    secret: str = Field(
        description="SecretResolver reference resolved to the HMAC shared secret.",
        json_schema_extra={"hot_reload": True},
    )


class WebhookSettings(BaseModel):
    """Webhook receiver configuration (Story 7.5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Master toggle for the webhook HTTP receiver.",
        json_schema_extra={"hot_reload": False},
    )
    bind_address: str = Field(
        default="127.0.0.1",
        description="IP the receiver binds to — defaults to loopback for safety.",
        json_schema_extra={"hot_reload": False},
    )
    port: int = Field(
        default=8766,
        ge=1,
        le=65_535,
        description="TCP port the receiver listens on.",
        json_schema_extra={"hot_reload": False},
    )
    sources: dict[str, WebhookSourceConfig] = Field(
        default_factory=dict,
        description="Per-source HMAC config keyed by url-path source name.",
        json_schema_extra={"hot_reload": True},
    )
