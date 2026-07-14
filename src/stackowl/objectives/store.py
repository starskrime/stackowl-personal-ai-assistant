"""ObjectiveStore — owner-scoped persistence for objectives + sub-goals + events.

Subclasses :class:`OwnedRepository` so every read/write is structurally bound to
one principal (cross-owner access is impossible). The three backing tables
(`objectives`, `objective_subgoals`, `objective_events`) are created in
migration 0066. The OwnedRepository helpers are table-parameterized, so one
store manages all three by passing the table name explicitly; ``_table`` is the
default (objectives) used for validation.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.objectives.model import (
    BlockerKind,
    ExpectedOutcome,
    Objective,
    ObjectiveEvent,
    ObjectiveStatus,
    Subgoal,
    SubgoalSpec,
    SubgoalStatus,
)
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tenancy.owned_repository import OwnedRepository

_OBJECTIVES = "objectives"
_SUBGOALS = "objective_subgoals"
_EVENTS = "objective_events"


class ObjectiveNotFoundError(LookupError):
    """Raised when an objective id is absent for the bound owner."""


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _dumps(value: Any) -> str | None:
    """JSON-encode a non-empty list/dict; empty/None → SQL NULL."""
    if not value:
        return None
    return json.dumps(value)


def _loads_list(text: Any) -> list[str]:
    if not text:
        return []
    parsed = json.loads(text)
    return list(parsed) if isinstance(parsed, list) else []


def _loads_dict(text: Any) -> dict[str, str | int]:
    if not text:
        return {}
    parsed = json.loads(text)
    return dict(parsed) if isinstance(parsed, dict) else {}


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _loads_verified(value: Any) -> bool | None:
    """Deserialize the tri-state ``verified`` column. NULL ⇒ None (not evaluated);
    a stored 1/0 ⇒ True/False. Tolerant: any non-NULL value coerces via truthiness."""
    if value is None:
        return None
    return bool(value)


def _dumps_outcome(outcome: ExpectedOutcome | None) -> str | None:
    """Serialize an ExpectedOutcome to JSON; None ⇒ SQL NULL (undeclared)."""
    return None if outcome is None else outcome.model_dump_json()


def _loads_outcome(text: Any) -> ExpectedOutcome | None:
    """Deserialize a stored ExpectedOutcome; NULL/garbage ⇒ None (no criterion).

    Tolerant by construction — a row written before this column existed, or any
    unparseable value, degrades to None (the legacy no-error path), never an error.
    """
    if not text:
        return None
    try:
        return ExpectedOutcome.model_validate_json(str(text))
    except ValueError:
        return None


def _dumps_depends_on(ids: list[str]) -> str | None:
    """JSON-encode subgoal_id dependencies; empty ⇒ SQL NULL (ready immediately)."""
    return _dumps(ids)


def _loads_depends_on(text: Any) -> list[str]:
    return _loads_list(text)


class ObjectiveStore(OwnedRepository):
    """Persist + query objectives, their ordered sub-goals, and an event log."""

    _table = _OBJECTIVES

    def __init__(self, db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        super().__init__(db, owner_id)

    # ----------------------------------------------------------- objectives

    async def create(self, objective: Objective) -> None:
        """INSERT an objective. ``owner_id`` is stamped from the bound owner."""
        log.engine.debug(
            "[objectives] store.create: entry",
            extra={"_fields": {"objective_id": objective.objective_id}},
        )
        now = _now().isoformat()
        await self._insert_owned(
            _OBJECTIVES,
            {
                "objective_id": objective.objective_id,
                "intent": objective.intent,
                "status": objective.status,
                "channel": objective.channel,
                "target_channels": _dumps(objective.target_channels),
                "target_addresses": _dumps(objective.target_addresses),
                "blocker": objective.blocker,
                "blocker_kind": objective.blocker_kind,
                "repo": objective.repo,
                "integration_branch": objective.integration_branch,
                "base_branch": objective.base_branch,
                "created_at": now,
                "updated_at": now,
            },
        )

    async def get(self, objective_id: str) -> Objective:
        """Return the objective or raise :class:`ObjectiveNotFoundError`."""
        rows = await self._fetch_owned(
            _OBJECTIVES, "objective_id = ?", (objective_id,)
        )
        if not rows:
            raise ObjectiveNotFoundError(objective_id)
        return self._row_to_objective(rows[0])

    async def list_objectives(
        self, status: ObjectiveStatus | None = None
    ) -> list[Objective]:
        """List objectives for the owner, optionally filtered by status."""
        if status is None:
            rows = await self._fetch_owned(_OBJECTIVES)
        else:
            rows = await self._fetch_owned(_OBJECTIVES, "status = ?", (status,))
        objectives = [self._row_to_objective(r) for r in rows]
        objectives.sort(key=lambda o: o.created_at)
        return objectives

    async def update_status(
        self,
        objective_id: str,
        status: ObjectiveStatus,
        *,
        blocker: str | None = None,
        blocker_kind: BlockerKind | None = None,
    ) -> None:
        """Transition the objective's status (and blocker + its CLASS when blocking).

        ``blocker`` / ``blocker_kind`` are written unconditionally so every non-block
        transition (``active`` / ``done`` / ``abandoned``) clears them — a recovered
        or finished objective must not carry a stale blocker classification."""
        await self._update_owned(
            _OBJECTIVES,
            set_sql="status = ?, blocker = ?, blocker_kind = ?, updated_at = ?",
            set_params=(status, blocker, blocker_kind, _now().isoformat()),
            where_sql="objective_id = ?",
            where_params=(objective_id,),
        )

    # ------------------------------------------------------------- sub-goals

    async def add_subgoals(
        self,
        objective_id: str,
        items: Sequence[str | SubgoalSpec],
        *,
        depth: int = 0,
    ) -> list[Subgoal]:
        """Append ordered sub-goals (positions continue after any existing ones).

        Each item is either a plain description string (legacy / no acceptance
        criterion) or a :class:`SubgoalSpec` carrying an OPTIONAL declared
        ``acceptance_criteria``, complexity estimate, and (Task #4) a
        ``depends_on`` list of INDICES into this SAME batch — resolved here to
        real subgoal_ids, since ids don't exist until insert. A bare string is
        normalized to a criterion-free, dependency-free spec, so every
        existing caller is unchanged (byte-identical). ``depth`` stamps
        ``decomposition_depth`` on every created row (Task 3); 0 (the
        default) for the objective's initial, top-level decomposition."""
        existing = await self.list_subgoals(objective_id)
        start = len(existing)
        specs = [SubgoalSpec(description=item) if isinstance(item, str) else item for item in items]
        now = _now()
        # First pass: mint every subgoal_id up front so depends_on indices
        # (which reference OTHER items in this same batch, including ones
        # that come later positionally) can all be resolved before any row
        # is inserted.
        ids = [f"sub-{uuid.uuid4().hex[:12]}" for _ in specs]
        created: list[Subgoal] = []
        for offset, spec in enumerate(specs):
            position = start + offset
            depends_on_ids = [ids[i] for i in spec.depends_on]
            created.append(
                await self._create_subgoal_row(
                    objective_id, position, spec, depth, now,
                    subgoal_id=ids[offset], depends_on=depends_on_ids,
                )
            )
        return created

    async def replace_subgoal_with_children(
        self,
        objective_id: str,
        subgoal: Subgoal,
        children: Sequence[SubgoalSpec],
        *,
        depth: int,
    ) -> list[Subgoal]:
        """Atomically replace ``subgoal`` with its decomposed ``children`` at its
        own run-order slot (Task 3 adaptive decomposition): shift every sub-goal
        at/after its position later by ``len(children)``, insert the children
        into the freed slot, and delete the now-superseded parent row — all in
        ONE committed transaction (mirrors the base+FTS atomicity pattern in
        :meth:`stackowl.memory.sqlite_bridge.SqliteMemoryBridge.delete`).

        This MUST be atomic: if the shift+inserts committed but the parent
        delete did not (a crash between two separately-committing statements),
        the original parent would survive at a shifted position, unchanged —
        and on restart the driver would run its already-inserted children AND
        then re-split/re-run the surviving parent, executing the same
        real-world action twice. Either all ``children`` land and ``subgoal``
        is gone, or none of this happened. A no-op on empty ``children``."""
        if not children:
            return []
        log.engine.debug(
            "[objectives] store.replace_subgoal_with_children: entry",
            extra={"_fields": {
                "objective_id": objective_id, "subgoal_id": subgoal.subgoal_id,
                "count": len(children), "depth": depth,
            }},
        )
        now = _now()
        created: list[Subgoal] = []
        async with self._db.transaction() as tx:
            await tx.execute(
                f"UPDATE {_SUBGOALS} SET position = position + ? "  # noqa: S608 — constant table name
                "WHERE owner_id = ? AND objective_id = ? AND position >= ?",
                (len(children), self._owner_id, objective_id, subgoal.position),
            )
            for offset, spec in enumerate(children):
                subgoal_id = f"sub-{uuid.uuid4().hex[:12]}"
                position = subgoal.position + offset
                await tx.execute(
                    f"INSERT INTO {_SUBGOALS} ("  # noqa: S608 — constant table name
                    "subgoal_id, owner_id, objective_id, position, description, "
                    "status, result, acceptance_criteria, attempts, verified, "
                    "task_id, estimated_complexity, decomposition_depth, "
                    "created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, 'pending', NULL, ?, 0, NULL, NULL, "
                    "?, ?, ?, ?)",
                    (
                        subgoal_id, self._owner_id, objective_id, position,
                        spec.description, _dumps_outcome(spec.acceptance_criteria),
                        spec.estimated_complexity, depth,
                        now.isoformat(), now.isoformat(),
                    ),
                )
                created.append(
                    Subgoal(
                        subgoal_id=subgoal_id,
                        owner_id=self._owner_id,
                        objective_id=objective_id,
                        position=position,
                        description=spec.description,
                        status="pending",
                        acceptance_criteria=spec.acceptance_criteria,
                        estimated_complexity=spec.estimated_complexity,
                        decomposition_depth=depth,
                        created_at=now,
                        updated_at=now,
                    )
                )
            await tx.execute(
                f"DELETE FROM {_SUBGOALS} WHERE owner_id = ? AND subgoal_id = ?",  # noqa: S608
                (self._owner_id, subgoal.subgoal_id),
            )
        log.engine.info(
            "[objectives] store.replace_subgoal_with_children: exit",
            extra={"_fields": {
                "objective_id": objective_id, "subgoal_id": subgoal.subgoal_id,
                "inserted": len(created),
            }},
        )
        return created

    async def _create_subgoal_row(
        self,
        objective_id: str,
        position: int,
        spec: SubgoalSpec,
        depth: int,
        now: datetime,
        *,
        subgoal_id: str | None = None,
        depends_on: list[str] | None = None,
    ) -> Subgoal:
        """Shared INSERT core for :meth:`add_subgoals` and :meth:`insert_subgoals_at`."""
        subgoal_id = subgoal_id or f"sub-{uuid.uuid4().hex[:12]}"
        depends_on = depends_on or []
        await self._insert_owned(
            _SUBGOALS,
            {
                "subgoal_id": subgoal_id,
                "objective_id": objective_id,
                "position": position,
                "description": spec.description,
                "status": "pending",
                "result": None,
                "acceptance_criteria": _dumps_outcome(spec.acceptance_criteria),
                "attempts": 0,
                "verified": None,
                "task_id": None,
                "estimated_complexity": spec.estimated_complexity,
                "decomposition_depth": depth,
                "depends_on": _dumps_depends_on(depends_on),
                "worktree_path": None,
                "story_branch": None,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            },
        )
        return Subgoal(
            subgoal_id=subgoal_id,
            owner_id=self._owner_id,
            objective_id=objective_id,
            position=position,
            description=spec.description,
            status="pending",
            acceptance_criteria=spec.acceptance_criteria,
            estimated_complexity=spec.estimated_complexity,
            decomposition_depth=depth,
            depends_on=depends_on,
            created_at=now,
            updated_at=now,
        )

    async def list_subgoals(self, objective_id: str) -> list[Subgoal]:
        """Return the objective's sub-goals ordered by position."""
        rows = await self._fetch_owned(
            _SUBGOALS, "objective_id = ?", (objective_id,)
        )
        subgoals = [self._row_to_subgoal(r) for r in rows]
        subgoals.sort(key=lambda s: s.position)
        return subgoals

    async def next_pending_subgoal(self, objective_id: str) -> Subgoal | None:
        """The lowest-position ``pending`` sub-goal, or None when none remain."""
        for subgoal in await self.list_subgoals(objective_id):
            if subgoal.status == "pending":
                return subgoal
        return None

    async def update_subgoal(
        self,
        subgoal_id: str,
        status: SubgoalStatus,
        *,
        result: str | None = None,
        task_id: str | None = None,
        attempts: int | None = None,
        verified: bool | None = None,
        worktree_path: str | None = None,
        story_branch: str | None = None,
    ) -> None:
        """Update a sub-goal's status and (optionally) result / task id / attempts /
        verification disposition.

        ``attempts`` and ``verified`` are only written when explicitly supplied —
        an absent argument leaves the stored value untouched (so the legacy callers
        that pass neither are byte-identical). ``verified`` is tri-state: pass
        ``True``/``False`` to stamp it; omit (``None``) to leave it as-is.
        ``worktree_path``/``story_branch`` follow the same "only written when
        supplied" convention (Task #4)."""
        sets = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, _now().isoformat()]
        if result is not None:
            sets.append("result = ?")
            params.append(result)
        if task_id is not None:
            sets.append("task_id = ?")
            params.append(task_id)
        if attempts is not None:
            sets.append("attempts = ?")
            params.append(attempts)
        if verified is not None:
            sets.append("verified = ?")
            params.append(1 if verified else 0)
        if worktree_path is not None:
            sets.append("worktree_path = ?")
            params.append(worktree_path)
        if story_branch is not None:
            sets.append("story_branch = ?")
            params.append(story_branch)
        await self._update_owned(
            _SUBGOALS,
            set_sql=", ".join(sets),
            set_params=tuple(params),
            where_sql="subgoal_id = ?",
            where_params=(subgoal_id,),
        )

    # ---------------------------------------------------------------- events

    async def append_event(
        self, objective_id: str, kind: str, detail: str | None = None
    ) -> None:
        """Append an activity-log event (id auto-increments)."""
        now = _now().isoformat()
        await self._insert_owned(
            _EVENTS,
            {
                "objective_id": objective_id,
                "at": now,
                "kind": kind,
                "detail": detail,
                "created_at": now,
            },
        )

    async def list_events(self, objective_id: str) -> list[ObjectiveEvent]:
        """Return the objective's events in chronological (insertion) order."""
        rows = await self._fetch_owned(
            _EVENTS, "objective_id = ?", (objective_id,)
        )
        rows.sort(key=lambda r: r.get("id") or 0)
        return [
            ObjectiveEvent(
                objective_id=str(r["objective_id"]),
                owner_id=str(r["owner_id"]),
                at=_parse_dt(r["at"]),
                kind=str(r["kind"]),
                detail=r.get("detail"),
            )
            for r in rows
        ]

    # --------------------------------------------------------------- mappers

    def _row_to_objective(self, row: dict[str, Any]) -> Objective:
        return Objective(
            objective_id=str(row["objective_id"]),
            owner_id=str(row["owner_id"]),
            intent=str(row["intent"]),
            status=row["status"],
            channel=row.get("channel"),
            target_channels=_loads_list(row.get("target_channels")),
            target_addresses=_loads_dict(row.get("target_addresses")),
            blocker=row.get("blocker"),
            blocker_kind=row.get("blocker_kind"),
            repo=row.get("repo"),
            integration_branch=row.get("integration_branch"),
            base_branch=row.get("base_branch"),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def _row_to_subgoal(self, row: dict[str, Any]) -> Subgoal:
        return Subgoal(
            subgoal_id=str(row["subgoal_id"]),
            owner_id=str(row["owner_id"]),
            objective_id=str(row["objective_id"]),
            position=int(row["position"]),
            description=str(row["description"]),
            status=row["status"],
            result=row.get("result"),
            acceptance_criteria=_loads_outcome(row.get("acceptance_criteria")),
            attempts=int(row.get("attempts") or 0),
            verified=_loads_verified(row.get("verified")),
            task_id=row.get("task_id"),
            estimated_complexity=float(row.get("estimated_complexity") or 0.0),
            decomposition_depth=int(row.get("decomposition_depth") or 0),
            depends_on=_loads_depends_on(row.get("depends_on")),
            worktree_path=row.get("worktree_path"),
            story_branch=row.get("story_branch"),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )
