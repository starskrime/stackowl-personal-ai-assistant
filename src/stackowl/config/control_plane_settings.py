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
        default="127.0.0.1",
        description=(
            "IP the control plane binds to. Loopback by default, which means it "
            "is reachable only from this machine — see ESC-172. Widening it is "
            "one setting, and the platform warns at WARNING when the bind cannot "
            "serve the operator it is configured for."
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
            "`admin`. The form exchanges these for the bearer token the API "
            "routes already use — it does not add a second way to authenticate."
        ),
        json_schema_extra={"hot_reload": True},
    )
    password: str = Field(
        default="admin",
        min_length=1,
        description=(
            "Password for the dashboard login form. Defaults to `admin` by "
            "operator request. Sensitive: auto-redacted wherever configuration "
            "is rendered, because `flatten` takes the LEAF key and "
            "`is_credential_name` flags `password` — verified, not assumed. "
            "THE PLATFORM WARNS WHILE THIS IS STILL THE DEFAULT: a default "
            "credential is only as safe as the bind, and `bind_address` is one "
            "setting away from a network (ESC-172)."
        ),
        json_schema_extra={"hot_reload": True, "sensitive": True},
    )

    @property
    def credentials_are_default(self) -> bool:
        """True while BOTH halves are still the shipped defaults.

        A property rather than a check at each call site: the warning is emitted
        at boot, the login route reports it on success and the page renders a
        banner, and three copies of `username == "admin" and password == "admin"`
        is the two-copies-of-one-rule shape this tree keeps paying for.
        """
        return self.username == "admin" and self.password == "admin"  # noqa: S105
