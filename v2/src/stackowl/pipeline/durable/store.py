"""DurableTaskStore — owner-scoped CRUD over the ``tasks`` table (Pass 3a).

Subclasses :class:`~stackowl.tenancy.OwnedRepository` so every read and write
is structurally bound to one principal: a task created by owner A can never be
read or mutated through a store bound to owner B. Inserts auto-stamp
``owner_id`` via :meth:`_insert_owned`; the status UPDATE carries the owner
predicate explicitly through :meth:`_execute_owned`.

Recovery semantics (claiming orphaned ``running`` tasks after a crash) belong
to the executor and are intentionally NOT implemented here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from stackowl.db.pool import DbPool
from stackowl.exceptions import DurableTaskNotFoundError
from stackowl.infra.observability import log
from stackowl.pipeline.durable.task import DurableTask, TaskStatus
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID, OwnedRepository

_SELECT_FIELDS = (
    "task_id, owner_id, goal, status, current_step, "
    "thread_id, result, created_at, updated_at"
)


class DurableTaskStore(OwnedRepository):
    """Owner-scoped persistence for :class:`DurableTask` rows."""

    _table = "tasks"

    def __init__(self, db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        super().__init__(db, owner_id)

    async def create(self, task: DurableTask) -> None:
        """Insert a new task. ``owner_id`` is stamped from the bound owner.

        Raises if ``task.owner_id`` disagrees with the bound owner (the
        OwnedRepository insert helper rejects cross-owner writes loudly).
        """
        # 1. ENTRY
        log.tasks.debug(
            "[tasks] store.create: entry",
            extra={"_fields": {
                "task_id": task.task_id, "owner_id": self._owner_id,
                "status": task.status,
            }},
        )
        await self._insert_owned(self._table, {
            "task_id": task.task_id,
            "owner_id": task.owner_id,
            "goal": task.goal,
            "status": task.status,
            "current_step": task.current_step,
            "thread_id": task.thread_id,
            "result": task.result,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
        })
        # 4. EXIT
        log.tasks.info(
            "[tasks] store.create: created",
            extra={"_fields": {"task_id": task.task_id, "owner_id": self._owner_id}},
        )

    async def get(self, task_id: str) -> DurableTask:
        """Return one task by id, owner-scoped.

        Raises :class:`DurableTaskNotFoundError` if no task with that id exists
        for the bound owner — a row owned by a different principal is invisible
        and therefore also raises.
        """
        # 1. ENTRY
        log.tasks.debug(
            "[tasks] store.get: entry",
            extra={"_fields": {"task_id": task_id, "owner_id": self._owner_id}},
        )
        rows = await self._fetch_owned(self._table, "task_id = ?", (task_id,))
        # 2. DECISION — miss is fail-loud (not None)
        if not rows:
            log.tasks.warning(
                "[tasks] store.get: not found for owner",
                extra={"_fields": {"task_id": task_id, "owner_id": self._owner_id}},
            )
            raise DurableTaskNotFoundError(task_id)
        task = _row_to_task(rows[0])
        # 4. EXIT
        log.tasks.debug(
            "[tasks] store.get: exit — hit",
            extra={"_fields": {"task_id": task_id, "status": task.status}},
        )
        return task

    async def list(self, status: TaskStatus | None = None) -> list[DurableTask]:
        """Return all tasks for the bound owner, optionally filtered by status."""
        # 1. ENTRY
        log.tasks.debug(
            "[tasks] store.list: entry",
            extra={"_fields": {"owner_id": self._owner_id, "status": status}},
        )
        # 2. DECISION — optional status predicate (owner clause added by helper)
        if status is None:
            rows = await self._fetch_owned(self._table)
        else:
            rows = await self._fetch_owned(self._table, "status = ?", (status,))
        tasks = [_row_to_task(r) for r in rows]
        # 4. EXIT
        log.tasks.debug(
            "[tasks] store.list: exit",
            extra={"_fields": {"owner_id": self._owner_id, "count": len(tasks)}},
        )
        return tasks

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        current_step: int | None = None,
        thread_id: str | None = None,
        result: str | None = None,
    ) -> None:
        """Owner-scoped UPDATE of a task's status and optional fields.

        Only the provided keyword fields are written; ``updated_at`` is always
        refreshed. The UPDATE carries an ``owner_id`` predicate so it can never
        touch another principal's row.
        """
        # 1. ENTRY
        log.tasks.debug(
            "[tasks] store.update_status: entry",
            extra={"_fields": {
                "task_id": task_id, "owner_id": self._owner_id, "status": status,
                "current_step": current_step,
                "set_thread_id": thread_id is not None,
                "set_result": result is not None,
            }},
        )
        # 2. DECISION — build the SET list dynamically from the supplied fields
        set_parts: list[str] = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, datetime.now(tz=UTC).isoformat()]
        if current_step is not None:
            set_parts.append("current_step = ?")
            params.append(current_step)
        if thread_id is not None:
            set_parts.append("thread_id = ?")
            params.append(thread_id)
        if result is not None:
            set_parts.append("result = ?")
            params.append(result)
        sql = (
            f"UPDATE {self._table} SET {', '.join(set_parts)} "  # noqa: S608 — table from class, columns are literals
            "WHERE owner_id = ? AND task_id = ?"
        )
        params.extend((self._owner_id, task_id))
        # 3. STEP — owner-scoped write (helper rejects SQL lacking owner_id)
        await self._execute_owned(sql, params)
        # 4. EXIT
        log.tasks.info(
            "[tasks] store.update_status: updated",
            extra={"_fields": {
                "task_id": task_id, "owner_id": self._owner_id, "status": status,
            }},
        )


def _row_to_task(row: dict[str, Any]) -> DurableTask:
    """Map one ``tasks`` row dict to a :class:`DurableTask`."""
    raw_thread = row.get("thread_id")
    raw_result = row.get("result")
    return DurableTask(
        task_id=str(row["task_id"]),
        owner_id=str(row["owner_id"]),
        goal=str(row["goal"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        current_step=int(row["current_step"]),
        thread_id=None if raw_thread is None else str(raw_thread),
        result=None if raw_result is None else str(raw_result),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )
