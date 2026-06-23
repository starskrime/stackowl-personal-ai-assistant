"""RuntimeSettings — process-topology configuration.

Master switch for the two-process split: a DURABLE gateway process holds client
connections (TUI, Telegram, …) while a RESTARTABLE core process runs the agent
logic, communicating over a local unix-domain socket. OFF by default — the
baseline runs the single-process in-process monolith (byte-identical behaviour).

Nests :class:`AutoRestartSettings`, which only takes effect when ``split_process``
is on (you can only restart the core without dropping the UI if the UI lives in
a separate, durable process).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from stackowl.config.auto_restart_settings import AutoRestartSettings


class RuntimeSettings(BaseModel):
    """Process-topology / runtime configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    split_process: bool = Field(
        default=True,
        description=(
            "Run the gateway and core as two processes over a local socket "
            "instead of one in-process monolith. ON by default — the durable "
            "gateway holds the TUI while the core can be restarted on code "
            "change. Set false to force the single-process compat path."
        ),
    )
    socket_path: str | None = Field(
        default=None,
        description=(
            "Override path for the gateway<->core unix-domain socket. None = "
            "derive from the StackOwl home run dir."
        ),
    )
    auto_restart: AutoRestartSettings = Field(default_factory=AutoRestartSettings)
