"""Domain models for the Objective Manager (1A).

These are the immutable-ish domain records; persistence lives in
:mod:`stackowl.objectives.store`. They mirror the shape of
:class:`stackowl.pipeline.durable.task.DurableTask` so the two substrates feel
the same to callers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)

#: Lifecycle of an objective.
#:
#: ``active``     being worked — the driver advances its pending sub-goals.
#: ``blocked``    suspended awaiting an irreversible decision only the owner can
#:                make (the act-on-reversible / ask-on-irreversible posture).
#: ``done``       every sub-goal finished successfully.
#: ``abandoned``  cancelled by the owner.
ObjectiveStatus = Literal["active", "blocked", "done", "abandoned"]

#: Lifecycle of a single sub-goal within an objective.
SubgoalStatus = Literal["pending", "running", "done", "failed", "blocked"]

#: WHY an objective is blocked (F-41) — drives whether the autonomous driver may
#: recover it. ``transient`` = stalled on a transient execution error (the in-tick
#: retry budget was spent); the driver re-queues it after a cooldown. ``decision``
#: = awaiting a genuinely irreversible/consequential choice only the owner can make
#: (or a verified-false outcome a clean retry would only re-assert); stays blocked
#: until a human steps in. ``None`` = legacy / unclassified, treated as ``decision``
#: (the conservative default — never auto-requeued).
BlockerKind = Literal["transient", "decision"]


class ExpectedOutcome(BaseModel, frozen=True):
    """A declared, deterministically-observable post-condition for a turn/sub-goal.

    The verification primitive's goal-level half: where ``ToolResult.verified``
    measures one tool's own artifact, an ``ExpectedOutcome`` is the GOAL's declared
    post-condition, observed against reality after the turn runs — catching the
    class where the tool cannot self-verify (e.g. ``shell`` running a no-op that
    exits 0). ``kind="none"`` (the default) is "no declared outcome" — the checker
    no-ops, so an absent/unset outcome is byte-identical to pre-acceptance behavior.

    Additive and minimal by design: ``artifact_dir`` is a directory that must gain
    a FRESH non-empty file during the turn (a relative path resolves under the
    workspace; ``None`` ⇒ the workspace root). ``description`` is human/LLM-readable
    context (logs + the future LLM-derived acceptance layer); it carries no behavior.
    """

    kind: Literal["none", "artifact"] = "none"
    artifact_dir: str | None = None
    description: str | None = None


class SubgoalSpec(BaseModel, frozen=True):
    """A decomposed step BEFORE persistence: its description plus an OPTIONAL
    declared acceptance criterion. The decomposer emits these; the store turns each
    into a :class:`Subgoal`. ``acceptance_criteria=None`` (the common case) keeps the
    sub-goal on the legacy no-error completion path (byte-identical)."""

    description: str = Field(..., min_length=1)
    acceptance_criteria: ExpectedOutcome | None = None


class Objective(BaseModel):
    """A persistent intent worked across many autonomous turns."""

    objective_id: str = Field(..., min_length=1)
    owner_id: str = Field(..., min_length=1)
    intent: str = Field(..., min_length=1)
    status: ObjectiveStatus = "active"
    #: Originating channel — the delivery context for progress/blocked pings.
    channel: str | None = None
    #: Durable delivery target captured at creation (mirrors the `jobs` row), so
    #: a scheduler tick with no live session can still report back to the owner.
    target_channels: list[str] = Field(default_factory=list)
    target_addresses: dict[str, str | int] = Field(default_factory=dict)
    #: Why the objective is blocked (set only when status == "blocked").
    blocker: str | None = None
    #: WHICH CLASS of block (F-41): ``transient`` ⇒ the driver re-queues after a
    #: cooldown; ``decision`` (or ``None``) ⇒ stays blocked until a human intervenes.
    #: Set only when status == "blocked"; cleared on every active/done transition.
    blocker_kind: BlockerKind | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Subgoal(BaseModel):
    """One ordered step of an objective, run as a single durable task."""

    subgoal_id: str = Field(..., min_length=1)
    owner_id: str = Field(..., min_length=1)
    objective_id: str = Field(..., min_length=1)
    position: int
    description: str = Field(..., min_length=1)
    status: SubgoalStatus = "pending"
    #: Produced answer (on done) or failure/block reason.
    result: str | None = None
    #: An OPTIONAL declared, deterministically-observable post-condition. When set,
    #: the driver gates ``done`` vs ``failed`` on the AcceptanceChecker's verdict
    #: instead of "no error thrown". None ⇒ legacy no-error path (byte-identical).
    acceptance_criteria: ExpectedOutcome | None = None
    #: How many times the driver has run this sub-goal and hit a failure. OPERATIONAL
    #: retry state (F-40): a transient stumble is retried up to a small ceiling before
    #: the objective escalates to ``blocked`` — NOT a learned lesson (positive-only
    #: learning is unaffected; nothing is mined from this count).
    attempts: int = 0
    #: Honest verification disposition (F-42), tri-state mirroring ``ToolResult.verified``:
    #: ``True`` the declared post-condition was observed against reality; ``False``
    #: completed but UNVERIFIED (no criterion to check — a clean run is not proof of
    #: effect); ``None`` legacy / not yet evaluated. A ``done`` sub-goal with
    #: ``verified is False`` is "completed but unverified" — completion is not over-claimed.
    verified: bool | None = None
    #: The durable task id that ran this sub-goal (for crash-resume legibility).
    task_id: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class ObjectiveEvent(BaseModel):
    """An entry in an objective's activity log (for `/agent objective status`)."""

    objective_id: str = Field(..., min_length=1)
    owner_id: str = Field(..., min_length=1)
    at: datetime
    kind: str = Field(..., min_length=1)
    detail: str | None = None
