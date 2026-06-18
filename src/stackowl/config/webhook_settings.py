"""Webhook-related settings sub-models (Story 7.5).

Kept in a dedicated module so :mod:`stackowl.config.settings` stays under
the B2 300-line cap.  Re-exported from :mod:`stackowl.config.settings`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WebhookSourceConfig(BaseModel):
    """A single registered webhook source — secret is resolved via SecretResolver.

    ``secret`` accepts the same syntax as every other secret reference in
    StackOwl: ``env:VAR_NAME``, ``file:/path``, or ``keychain:<service>``.

    Replay protection (C7 / F132): every source MUST declare at least one
    anti-replay mechanism — a ``timestamp_header`` (signed-timestamp window, the
    strongest, attacker-immutable because the timestamp is HMAC-bound) OR a
    ``delivery_id_header`` (sender-supplied dedup id). A source declaring NEITHER
    is a configuration error, raised loudly at config-load (fail-closed, R6) —
    never a silent weakening of an existing source.
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
    timestamp_header: str | None = Field(
        default=None,
        description=(
            "Request header carrying the signed timestamp. When set, the HMAC is "
            "computed over `{timestamp}.` + body (Stripe scheme) and a stale "
            "timestamp outside replay_tolerance_s is rejected. The strongest "
            "anti-replay mechanism (the timestamp is signed, so unforgeable)."
        ),
        json_schema_extra={"hot_reload": True},
    )
    delivery_id_header: str | None = Field(
        default=None,
        description=(
            "Request header carrying a sender-supplied delivery id, folded into "
            "the server-derived dedup key as an EXTRA uniqueness signal (never "
            "trusted alone — attacker-controlled on replay)."
        ),
        json_schema_extra={"hot_reload": True},
    )
    replay_tolerance_s: int = Field(
        default=300,
        ge=1,
        description="Max abs() age of a signed timestamp, in seconds, before reject.",
        json_schema_extra={"hot_reload": True},
    )

    @model_validator(mode="after")
    def _require_anti_replay_mechanism(self) -> WebhookSourceConfig:
        """Fail-closed: a source must declare ≥1 anti-replay mechanism (R6)."""
        if not self.timestamp_header and not self.delivery_id_header:
            raise ValueError(
                "webhook source must declare an anti-replay mechanism: set "
                "'timestamp_header' (preferred — signed-timestamp window) or "
                "'delivery_id_header' (dedup id). A source with neither is "
                "replayable and is rejected at config load (C7 / F132)."
            )
        return self


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
