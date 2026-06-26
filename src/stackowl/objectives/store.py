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
    ) -> None:
        """Transition the objective's status (and blocker when blocking)."""
        await self._update_owned(
            _OBJECTIVES,
            set_sql="status = ?, blocker = ?, updated_at = ?",
            set_params=(status, blocker, _now().isoformat()),
            where_sql="objective_id = ?",
            where_params=(objective_id,),
        )

    # ------------------------------------------------------------- sub-goals

    async def add_subgoals(
        self, objective_id: str, items: Sequence[str | SubgoalSpec]
    ) -> list[Subgoal]:
        """Append ordered sub-goals (positions continue after any existing ones).

        Each item is either a plain description string (legacy / no acceptance
        criterion) or a :class:`SubgoalSpec` carrying an OPTIONAL declared
        ``acceptance_criteria``. A bare string is normalized to a criterion-free
        spec, so every existing caller is unchanged (byte-identical)."""
        existing = await self.list_subgoals(objective_id)
        start = len(existing)
        created: list[Subgoal] = []
        now = _now()
        for offset, item in enumerate(items):
            spec = SubgoalSpec(description=item) if isinstance(item, str) else item
            subgoal_id = f"sub-{uuid.uuid4().hex[:12]}"
            position = start + offset
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
                    "task_id": None,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                },
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
                    created_at=now,
                    updated_at=now,
                )
            )
        return created

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
    ) -> None:
        """Update a sub-goal's status and (optionally) its result / task id."""
        sets = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, _now().isoformat()]
        if result is not None:
            sets.append("result = ?")
            params.append(result)
        if task_id is not None:
            sets.append("task_id = ?")
            params.append(task_id)
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
            task_id=row.get("task_id"),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )
