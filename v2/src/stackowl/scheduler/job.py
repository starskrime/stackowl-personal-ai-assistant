"""Job and JobResult — scheduler domain models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Job(BaseModel):
    """A persistent scheduled job entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    handler_name: str
    schedule: str
    idempotency_key: str
    last_run_at: str | None
    next_run_at: str
    status: Literal["pending", "running", "completed", "failed"]
    retry_count: int = 0
    failure_count: int = 0
    last_error: str | None = None
    enabled: bool = True
    replay_missed: bool = False
    primary_channel: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    # Durable delivery target (C1/F104) — the recipient persisted on the job row
    # so a cron-born poll (no session, no TraceContext, no channel) can address
    # its send from durable state. ``target_channels`` is the list of channel
    # names to deliver to; ``target_addresses`` maps each channel name to its
    # channel-NATIVE destination token (telegram ``int`` chat id, slack ``str``
    # channel id). Empty/None on a legacy row means no durable recipient was
    # stamped — the DeliverySpec resolver then yields no pair and the caller
    # records the send as undeliverable (never ``delivered``, never ``_last_*``).
    target_channels: list[str] = Field(default_factory=list)
    target_addresses: dict[str, str | int] = Field(default_factory=dict)


class JobResult(BaseModel):
    """The outcome of a single job execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    success: bool
    output: str | None
    error: str | None
    duration_ms: float
    metadata: dict[str, Any] = Field(default_factory=dict)
