"""Control-plane configuration.

The token is deliberately NOT a field here. It is minted on first boot and kept
through :func:`stackowl.config.secret_writer.store_secret` (OS keyring, else a
0600 file), so its plaintext never enters a YAML file an operator might paste
into a bug report. See ``docs/reference-mapping/designs/A05.1.md``, invariant I4.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ControlPlaneSettings(BaseModel):
    """Configuration for the customer-facing control plane."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = Field(
        default=True,
        description=(
            "Whether the control plane serves. Defaults ON: a capability ships "
            "enabled here, and an operator switch is an opt-out only."
        ),
        json_schema_extra={"hot_reload": False},
    )
    bind_address: str = Field(
        default="0.0.0.0",  # noqa: S104 — every interface, deliberately; see below
        description=(
            "IP the control plane binds to. EVERY INTERFACE by default, so a "
            "customer who clones this repo and runs it reaches the dashboard "
            "from their own browser without editing any configuration. Narrow "
            "it to 127.0.0.1 to make the dashboard reachable only from the "
            "machine it runs on; the platform warns at WARNING when the bind "
            "cannot serve the operator it is configured for. "
            "THE LOGIN IS WHAT STANDS IN FRONT OF IT, AND NO PUBLISHED PASSWORD "
            "OPENS IT: a fresh install has no password and serves no data until "
            "the owner sets one with a one-time setup code — sent to the owner's "
            "Telegram when exactly one owner is configured, shown on the platform's "
            "terminal when it has one, and always printed by `stackowl control-plane "
            "reset-password` on the host."
        ),
        json_schema_extra={"hot_reload": False},
    )
    port: int = Field(
        default=8787,
        ge=1,
        le=65_535,
        description="TCP port the control plane listens on.",
        json_schema_extra={"hot_reload": False},
    )
    username: str = Field(
        default="admin",
        min_length=1,
        description=(
            "Username for the dashboard login form. Operator-set; defaults to "
            "`admin`. The form exchanges it and the password for the bearer token "
            "the API routes already use — it does not add a second way to "
            "authenticate. THE PASSWORD IS NOT A SETTING: it is set on the "
            "dashboard with a one-time setup code and kept only as a salted hash "
            "in the platform's secret store; `stackowl control-plane "
            "reset-password` on the host clears it."
        ),
        json_schema_extra={"hot_reload": True},
    )
