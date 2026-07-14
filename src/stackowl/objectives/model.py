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
    #: The decomposer's own complexity estimate for this ONE step (Task 3 adaptive
    #: decomposition), on a 0.0 (trivial, one concrete action) to 1.0 (bundles
    #: multiple actions — worth splitting further) scale. Populated by the SAME
    #: decomposition LLM call that produces ``description`` (no extra round-trip):
    #: parsed from an optional trailing ``<<complexity: N>>`` marker. Default 0.0
    #: ("no signal") is the conservative choice — an unparsed reply, or the
    #: fail-safe single-spec fallback, never triggers recursive decomposition.
    estimated_complexity: float = Field(default=0.0, ge=0.0, le=1.0)
    #: Task #4 — indices into the SAME decomposition batch this spec's story
    #: depends on (e.g. story 2 depending on story 0 emits `depends_on=[0]`).
    #: Resolved to real subgoal_ids by the store on insert. Empty (default,
    #: every existing caller) ⇒ ready immediately.
    depends_on: list[int] = Field(default_factory=list)


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
    #: Task #4 (coding-capability build plan) — set only for an EPIC objective.
    #: None (every existing row, every plain-objective caller) ⇒ the linear,
    #: single-subgoal-per-tick driver path, byte-identical to today.
    repo: str | None = None
    #: The epic's internal integration branch (e.g. "stackowl/epic-obj-1"),
    #: branched off base_branch when the epic starts. Set together with repo.
    integration_branch: str | None = None
    #: The branch `objective-merge` targets — captured via `git branch
    #: --show-current` in `repo` at epic creation.
    base_branch: str | None = None
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
    #: The decomposer's complexity estimate carried over from the originating
    #: :class:`SubgoalSpec` (Task 3 adaptive decomposition). 0.0 default for every
    #: pre-existing row and every legacy caller that never set it.
    estimated_complexity: float = Field(default=0.0, ge=0.0, le=1.0)
    #: How many recursive decompositions produced this sub-goal (Task 3); 0 =
    #: top-level (the objective's initial decomposition). The driver refuses to
    #: split a sub-goal further once this reaches ``_MAX_DECOMPOSITION_DEPTH``
    #: (``objectives/driver.py``), so a persistently "complex" reply can never
    #: recurse without bound.
    decomposition_depth: int = Field(default=0, ge=0)
    #: Task #4 — subgoal_ids that must reach status "done" before this story
    #: is ready to launch. Empty (default, every existing row) ⇒ ready
    #: immediately — matches today's linear behavior.
    depends_on: list[str] = Field(default_factory=list)
    #: Set once this story's worktree is created (epic path only).
    worktree_path: str | None = None
    #: Set once this story's scratch branch is created (epic path only).
    story_branch: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class ObjectiveEvent(BaseModel):
    """An entry in an objective's activity log (for `/agent objective status`)."""

    objective_id: str = Field(..., min_length=1)
    owner_id: str = Field(..., min_length=1)
    at: datetime
    kind: str = Field(..., min_length=1)
    detail: str | None = None
