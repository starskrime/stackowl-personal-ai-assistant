"""DurableTask — the persisted unit of long-running agentic work (Pass 3a).

A :class:`DurableTask` is the durable-state record for one goal that the
executor (wired in a later pass) drives across crashes and restarts. It is
owner-scoped: every task belongs to exactly one principal via ``owner_id``.

This module defines ONLY the immutable-ish domain model + its status
vocabulary. Persistence lives in :mod:`stackowl.pipeline.durable.store`; the
executor/graph wiring is explicitly out of scope for this pass.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from stackowl.authz.bounds import BoundsSpec

#: Lifecycle of a durable task.
#:
#: ``pending``    created, not yet started.
#: ``running``    actively executing a step.
#: ``recovering`` CLAIMED by startup crash-recovery (B4) for re-drive — a
#:                transient ownership latch atomically taken from ``running`` so
#:                a second worker can never double-recover the same orphan. The
#:                claimant transitions it back to ``running`` before resuming and
#:                finalizes it to a terminal status from there.
#: ``parked``     suspended awaiting an external signal (e.g. human/approval).
#: ``completed``  finished successfully (``result`` populated).
#: ``failed``     terminated with an unrecoverable error (``result`` = reason).
TaskStatus = Literal[
    "pending", "running", "recovering", "parked", "completed", "failed"
]


class DurableTask(BaseModel):
    """A single durable goal tracked across the agent's lifetime."""

    task_id: str = Field(..., min_length=1)
    owner_id: str = Field(..., min_length=1)
    goal: str = Field(..., min_length=1)
    status: TaskStatus
    current_step: int = 0
    #: LangGraph checkpoint thread id — set by the executor in a later pass.
    thread_id: str | None = None
    result: str | None = None
    #: Originating owl persona (threaded from the creating PipelineState). NULL
    #: on legacy rows created before migration 0047 — B4 recovery falls back to
    #: the documented 'secretary' default when this is None.
    owl_name: str | None = None
    #: Originating channel (cli/telegram/...) of the durable goal. NULL on legacy
    #: rows — B4 recovery falls back to the documented 'cli' default when None.
    channel: str | None = None
    #: Snapshot of the owl's bounds at task CREATION — the resume-monotonicity
    #: ceiling (E2-S2). NULL on legacy rows (pre-0048) and on a task created under
    #: an unbounded owl → None → resume uses the owl's current bounds.
    creation_ceiling: BoundsSpec | None = None
    #: Preflight-planner least-privilege envelope (E2-S3). NULL when the planner
    #: declined/failed or for legacy rows. Telemetry + presentation only.
    task_envelope: BoundsSpec | None = None
    created_at: datetime
    updated_at: datetime
