"""Job and JobResult — scheduler domain models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# What KIND of effect a job claims — mirrors ToolResult.verified's tri-state
# reality check, adapted for the scheduler's heterogeneous effect shapes.
# "delivery" = sent something to a channel/user, "state_change" = mutated
# durable state without delivering, "read_only" = no mutation (sweeps, health
# checks).
JobEffectClass = Literal["delivery", "state_change", "read_only"]


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
    # STEER-5/F113 — the SEPARATE retry slot. NULL = no retry pending (steady
    # state). A failed run schedules a retry here without touching next_run_at (the
    # canonical recurring cadence); cleared on success. Read-only on the model —
    # the scheduler writes it via direct UPDATEs, not through insert_job.
    retry_at: str | None = None
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
    # VERIFICATION (the reality check, distinct from `success` the self-report).
    # Same tri-state semantics as `ToolResult.verified` (tools/base.py).
    # None  ⇒ not checked — falls back to `success` (byte-identical to pre-
    #         verification behavior; the default for every un-migrated handler).
    # True  ⇒ the claimed effect was OBSERVED in reality.
    # False ⇒ the handler claimed success but reality disagreed (the effect was
    #         not confirmed). `success` is NOT mutated — the claim-vs-confirmation
    #         distinction is preserved. `tools.verification.is_trustworthy_success`
    #         collapses the two for the scheduler's dispatch decision.
    verified: bool | None = None
    # What KIND of effect this job claims. Default "state_change" is the
    # conservative default for un-migrated handlers (PB6b sets the real value
    # per handler).
    effect_class: JobEffectClass = "state_change"
    # Free-form description/locator of what was checked to produce `verified`
    # (a DB row, a sent message, a pellet — job effects are heterogeneous, so
    # this is a description string rather than a path). None when `verified`
    # is None (nothing was checked).
    post_condition: str | None = None
