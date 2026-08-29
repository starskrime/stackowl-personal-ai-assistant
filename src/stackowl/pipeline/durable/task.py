"""DurableTask — the persisted unit of long-running agentic work (Pass 3a).

A :class:`DurableTask` is the durable-state record for one goal that the
executor (wired in a later pass) drives across crashes and restarts. It is
owner-scoped: every task belongs to exactly one principal via ``owner_id``.

This module defines ONLY the immutable-ish domain model + its status
vocabulary. Persistence lives in :mod:`stackowl.pipeline.durable.store`; the
executor/graph wiring is explicitly out of scope for this pass.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
#: ``dead_letter`` the ONE loop's ending that is not success (migration 0119). The
#:                 attempt ceiling was hit, or the failure was permanent. The row
#:                 STAYS, visible and explained, and is escalated — a loop that
#:                 silently drops work is worse than one that fails loudly.
TaskStatus = Literal[
    "pending", "running", "recovering", "parked", "completed", "failed", "dead_letter"
]

#: A task never retried more times than this unless its row says otherwise.
#: Bakir, 2026-08-17: "each task we may have around thirty limit to try. And this
#: thirty can be in configuration" — so it is a per-row column with this default,
#: not a constant compiled into the loop.
DEFAULT_MAX_ATTEMPTS = 30


class DurableTask(BaseModel):
    """A single durable goal tracked across the agent's lifetime."""

    task_id: str = Field(..., min_length=1)
    #: Defaulted so enqueue() can stamp the store's own principal; a caller
    #: states what the task MEANS, not which tenant bookkeeping it belongs to.
    owner_id: str = Field(default="principal-default", min_length=1)
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
    #: The conversation LANE this work belongs to, so invariant I4 can ask whether
    #: a lane has work in flight before expiring it (D01.7 Q12). NULL on legacy rows
    #: and on any task not born from a turn; a NULL lane never matches a real one.
    session_key: str | None = None
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
    #: Link to the parent durable task when this task is a delegated child (D1).
    #: NULL ⇒ a root goal; non-NULL ⇒ a child spawned through delegate_task.
    parent_task_id: str | None = None
    #: The delegating owl name (audit + return-path legibility). NULL for roots.
    parent_owl: str | None = None
    #: The parent's delegate_task idempotency key this child was minted from
    #: (D1 §5; audit + reaper). NULL for roots.
    delegate_key: str | None = None
    #: Single-owner execution lease holder (D1 §7). NULL ⇒ unclaimed.
    lease_owner: str | None = None
    #: True when a timed-out child was tombstoned so a slow eventual commit is
    #: neutralized and the next ladder rung gets a fresh id (D1 §9).
    superseded: bool = False
    # ---- the ONE loop (migration 0119) -----------------------------------
    #: WHERE the outcome must land for this task to be done — "telegram:72055773",
    #: "cli", a channel and address. Bakir, 2026-08-17: "if it's delivered to me,
    #: it means loop is completed." NULL ⇒ the task has no destination of its own
    #: (a pure sub-goal whose parent delivers), and completion is then the parent's.
    destination: str | None = None
    #: What "done" MEANS for this task, in words the loop can check against. The
    #: achievement condition, distinct from the goal: the goal is what to do, this
    #: is how you know it happened.
    achievement: str | None = None
    #: Proof the outcome reached ``destination``. Set ONLY by mark_delivered.
    #: A ``completed`` row without this is a self-report, not a delivery.
    delivered_at: datetime | None = None
    #: When this row's TERMINAL outcome was surfaced — announced to whoever was
    #: waiting, or reviewed and found to have nobody waiting. Distinct from
    #: ``delivered_at``, which is proof the ANSWER arrived: "we told someone it
    #: stopped" and "the answer landed" are different facts, and conflating them
    #: would claim a delivery that never happened.
    #:
    #: Exists because `revive_undelivered_failures` skips dead letters on the
    #: grounds that the status was "already made AND ANNOUNCED" — which was untrue
    #: for 74 live rows, 72 of them unaddressable, leaving debt no sweep could see.
    acknowledged_at: datetime | None = None
    #: How many times this has been tried, and the per-task ceiling.
    attempt_count: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    #: What went wrong LAST time, so the next attempt is constrained rather than
    #: blind. Bakir: "adding previous failure details... next loop learns from that
    #: experience."
    last_error: str | None = None
    last_failure_class: str | None = None
    #: The STRUCTURED half of that learning: capabilities already proven not to work
    #: for this goal. Accumulates across attempts — attempt three must know what
    #: attempts one and two burned. A list stays bounded where pasted error prose
    #: does not.
    banned_capabilities: tuple[str, ...] = ()
    #: Objective-work fields, absorbed from objective_subgoals (migration 0126).
    #: A subgoal duplicated 11 of its 18 columns onto this table — including
    #: STATUS, which is how 44 subgoals read pending/running on 2026-08-28 while
    #: no task was running. One row per unit of work, one status.
    #: All None for a chat turn, a cron task or a retry.
    position: int | None = None
    verified: bool | None = None
    estimated_complexity: str | None = None
    decomposition_depth: int | None = None
    worktree_path: str | None = None
    story_branch: str | None = None
    #: Backoff. Without it a failed row is re-claimed on the next tick, turning one
    #: broken task into a hot loop.
    next_attempt_at: datetime | None = None
    #: When this row's lease dies. ``lease_owner`` alone cannot tell "a worker holds
    #: this" from "a worker DIED holding this", so without an expiry the row would
    #: sit claimed forever and the work would leak silently.
    lease_expires_at: datetime | None = None
    #: The graph. Task ids that must have landed before this one may be claimed.
    #: Bakir: "one loop may need small other loops" — a sub-task is another ROW, so
    #: the graph is edges between rows rather than a second system.
    depends_on: tuple[str, ...] = ()
    #: What triggered this — chat / schedule / subgoal / incident. Recorded so the
    #: loop can be asked what it is serving instead of that being inferred.
    trigger_kind: str | None = None
    #: Thirty retries must not mean thirty side effects.
    idempotency_key: str | None = None

    # Defaulted so a caller enqueuing a task states only what it MEANS, not the
    # bookkeeping. The store still stamps updated_at on every transition.
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
