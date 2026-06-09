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

from stackowl.authz.bounds import BoundsSpec
from stackowl.db.pool import DbPool
from stackowl.exceptions import DurableTaskNotFoundError
from stackowl.infra.observability import log
from stackowl.pipeline.durable.task import DurableTask, TaskStatus
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID, OwnedRepository

_SELECT_FIELDS = (
    "task_id, owner_id, goal, status, current_step, "
    "thread_id, result, owl_name, channel, creation_ceiling, task_envelope, "
    "parent_task_id, parent_owl, delegate_key, lease_owner, superseded, "
    "created_at, updated_at"
)

# Minimal fields for checkpoint read — avoids pulling the full task row when
# only the blob is needed (future optimisation hook; currently the full row is
# fetched and the checkpoint_blob column is read off it).
_CHECKPOINT_BLOB_FIELD = "checkpoint_blob"


class DurableTaskStore(OwnedRepository):
    """Owner-scoped persistence for :class:`DurableTask` rows."""

    _table = "tasks"

    def __init__(self, db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        super().__init__(db, owner_id)

    async def _require_owned(self, task_id: str, *, op: str) -> None:
        """Fail loud unless ``task_id`` exists for the bound owner.

        :class:`~stackowl.db.pool.DbPool.execute` returns no rows-affected count,
        so an owner-scoped UPDATE against a non-existent (or cross-owner) row
        silently no-ops. The owner-scoped UPDATEs in :meth:`update_status` /
        :meth:`save_checkpoint` call this FIRST so a durable write against a
        missing task raises :class:`DurableTaskNotFoundError` instead of
        completing a "durable" drive with no persisted state. Reuses the same
        owner-scoped ``_fetch_owned`` predicate :meth:`get` uses, so a row owned
        by a different principal is invisible and therefore also raises.
        """
        rows = await self._fetch_owned(self._table, "task_id = ?", (task_id,))
        if not rows:
            log.tasks.error(
                "[tasks] store: owner-scoped write on a missing task — raising",
                extra={"_fields": {
                    "task_id": task_id, "owner_id": self._owner_id, "op": op,
                }},
            )
            raise DurableTaskNotFoundError(task_id)

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
            "owl_name": task.owl_name,
            "channel": task.channel,
            "creation_ceiling": (
                task.creation_ceiling.model_dump_json()
                if task.creation_ceiling is not None
                else None
            ),
            "task_envelope": (
                task.task_envelope.model_dump_json()
                if task.task_envelope is not None
                else None
            ),
            "parent_task_id": task.parent_task_id,
            "parent_owl": task.parent_owl,
            "delegate_key": task.delegate_key,
            "lease_owner": task.lease_owner,
            "superseded": 1 if task.superseded else 0,
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
        # 2b. DECISION — fail loud on a missing/wrong-owner row. DbPool.execute
        #     reports no rowcount, so an owner-scoped UPDATE against a row that
        #     does not exist (or belongs to another principal) would silently
        #     no-op — a "durable" drive would advance with NO status change and
        #     NO error. Verify existence under the bound owner FIRST and raise.
        await self._require_owned(task_id, op="update_status")
        # 3. STEP — owner-scoped write (helper rejects SQL lacking owner_id)
        await self._execute_owned(sql, params)
        # 4. EXIT
        log.tasks.info(
            "[tasks] store.update_status: updated",
            extra={"_fields": {
                "task_id": task_id, "owner_id": self._owner_id, "status": status,
            }},
        )


    async def claim_for_recovery(self, task_id: str) -> bool:
        """Atomically CLAIM an orphaned task for crash-recovery.

        A compare-and-swap: ``UPDATE tasks SET status='recovering' WHERE
        owner_id=? AND task_id=? AND status IN ('running','recovering')``.
        Exactly one caller can win — the row only transitions out of the claimed
        set once (an idempotent ``recovering -> recovering`` still costs the WHERE
        match, so a concurrent second writer sees rows-affected=0 and must skip).

        Both ``running`` AND ``recovering`` are claimable because at STARTUP the
        prior process is DEAD: there are no concurrent live drives, so a
        ``recovering`` row is necessarily a STALE orphan left when a process was
        killed BETWEEN the claim (running -> recovering) and the resume. Without
        claiming ``recovering`` such a task would be stuck forever (the old sweep
        listed only ``running``). This is still atomic, still owner-scoped, and a
        single CAS winner. Returns ``True`` iff THIS call claimed the row.

        Owner-scoped: the WHERE carries ``owner_id`` so a row owned by a
        different principal can never be claimed through this store.
        """
        # 1. ENTRY
        log.tasks.debug(
            "[tasks] store.claim_for_recovery: entry",
            extra={"_fields": {"task_id": task_id, "owner_id": self._owner_id}},
        )
        sql = (
            f"UPDATE {self._table} SET status = ?, updated_at = ? "  # noqa: S608 — table from class
            "WHERE owner_id = ? AND task_id = ? AND status IN ('running', 'recovering')"
        )
        params = [
            "recovering",
            datetime.now(tz=UTC).isoformat(),
            self._owner_id,
            task_id,
        ]
        # 2. DECISION — the helper rejects SQL lacking an owner_id predicate; this
        #    one carries it, so the CAS is structurally owner-scoped.
        if "owner_id" not in sql.lower():  # pragma: no cover — defensive
            raise ValueError("claim_for_recovery SQL must carry an owner_id predicate")
        # 3. STEP — atomic CAS; rows-affected tells us if WE won the race.
        affected = await self._db.execute_returning_rowcount(sql, params)
        claimed = affected == 1
        # 4. EXIT
        log.tasks.info(
            "[tasks] store.claim_for_recovery: exit",
            extra={"_fields": {
                "task_id": task_id, "owner_id": self._owner_id,
                "claimed": claimed, "rows_affected": affected,
            }},
        )
        return claimed

    async def save_checkpoint(self, task_id: str, blob: str) -> None:
        """Persist the serialised :class:`~stackowl.pipeline.durable.react_checkpoint.ReActCheckpoint`
        blob on the task row (owner-scoped UPDATE).

        The column ``checkpoint_blob`` is written unconditionally — each call
        overwrites the previous snapshot.  The ``updated_at`` timestamp is NOT
        refreshed here because a checkpoint write is a sub-step event (not a
        status transition); callers that want to advance ``current_step`` use
        :meth:`update_status`.

        The UPDATE carries ``owner_id`` in its WHERE clause so
        :meth:`~stackowl.tenancy.OwnedRepository._execute_owned` accepts it and
        a task owned by a different principal can never be written.
        """
        # 1. ENTRY
        log.tasks.debug(
            "[tasks] store.save_checkpoint: entry",
            extra={"_fields": {
                "task_id": task_id, "owner_id": self._owner_id,
                "blob_len": len(blob),
            }},
        )
        # 2. DECISION — unconditional overwrite; owner predicate enforces isolation
        sql = (
            f"UPDATE {self._table} SET checkpoint_blob = ? "  # noqa: S608 — table from class
            "WHERE owner_id = ? AND task_id = ?"
        )
        # 2b. DECISION — fail loud on a missing/wrong-owner row (see
        #     update_status). Without a rowcount, a no-op UPDATE would otherwise
        #     leave a "durable" drive with NO persisted checkpoint and NO error.
        await self._require_owned(task_id, op="save_checkpoint")
        # 3. STEP — owner-scoped write
        await self._execute_owned(sql, [blob, self._owner_id, task_id])
        # 4. EXIT
        log.tasks.info(
            "[tasks] store.save_checkpoint: saved",
            extra={"_fields": {"task_id": task_id, "owner_id": self._owner_id}},
        )

    async def load_checkpoint(self, task_id: str) -> str | None:
        """Return the raw checkpoint blob for ``task_id``, or ``None`` if no
        checkpoint has been saved yet.

        Owner-scoped: only the row belonging to the bound owner is readable.
        A task that exists but has no checkpoint (``checkpoint_blob IS NULL``)
        returns ``None`` — not an error.  A task that does not exist for the
        bound owner also returns ``None`` (invisible-is-missing semantics,
        consistent with the exactly-once / replay contract).
        """
        # 1. ENTRY
        log.tasks.debug(
            "[tasks] store.load_checkpoint: entry",
            extra={"_fields": {"task_id": task_id, "owner_id": self._owner_id}},
        )
        # 2. DECISION — fetch the task row scoped to this owner; missing = None
        rows = await self._fetch_owned(self._table, "task_id = ?", (task_id,))
        if not rows:
            log.tasks.debug(
                "[tasks] store.load_checkpoint: task not found for owner — returning None",
                extra={"_fields": {"task_id": task_id, "owner_id": self._owner_id}},
            )
            return None
        # 3. STEP — extract the blob (may be NULL in the DB)
        raw = rows[0].get(_CHECKPOINT_BLOB_FIELD)
        blob: str | None = None if raw is None else str(raw)
        # 4. EXIT
        log.tasks.debug(
            "[tasks] store.load_checkpoint: exit",
            extra={"_fields": {
                "task_id": task_id, "owner_id": self._owner_id,
                "has_blob": blob is not None,
            }},
        )
        return blob


def _row_to_task(row: dict[str, Any]) -> DurableTask:
    """Map one ``tasks`` row dict to a :class:`DurableTask`."""
    raw_thread = row.get("thread_id")
    raw_result = row.get("result")
    raw_owl = row.get("owl_name")
    raw_channel = row.get("channel")
    raw_ceiling = row.get("creation_ceiling")
    ceiling: BoundsSpec | None = (
        BoundsSpec.model_validate_json(str(raw_ceiling))
        if raw_ceiling is not None
        else None
    )
    raw_env = row.get("task_envelope")
    envelope: BoundsSpec | None = (
        BoundsSpec.model_validate_json(str(raw_env))
        if raw_env is not None
        else None
    )
    raw_parent = row.get("parent_task_id")
    raw_parent_owl = row.get("parent_owl")
    raw_delegate_key = row.get("delegate_key")
    raw_lease = row.get("lease_owner")
    raw_superseded = row.get("superseded")
    return DurableTask(
        task_id=str(row["task_id"]),
        owner_id=str(row["owner_id"]),
        goal=str(row["goal"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        current_step=int(row["current_step"]),
        thread_id=None if raw_thread is None else str(raw_thread),
        result=None if raw_result is None else str(raw_result),
        owl_name=None if raw_owl is None else str(raw_owl),
        channel=None if raw_channel is None else str(raw_channel),
        creation_ceiling=ceiling,
        task_envelope=envelope,
        parent_task_id=None if raw_parent is None else str(raw_parent),
        parent_owl=None if raw_parent_owl is None else str(raw_parent_owl),
        delegate_key=None if raw_delegate_key is None else str(raw_delegate_key),
        lease_owner=None if raw_lease is None else str(raw_lease),
        superseded=bool(raw_superseded),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )
