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

import builtins
from datetime import UTC, datetime, timedelta
from typing import Any

from stackowl.authz.bounds import BoundsSpec
from stackowl.db.pool import DbPool
from stackowl.exceptions import DurableTaskNotFoundError
from stackowl.infra.observability import log
from stackowl.pipeline.durable.task import DEFAULT_MAX_ATTEMPTS, DurableTask, TaskStatus
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID, OwnedRepository

_SELECT_FIELDS = (
    "task_id, owner_id, goal, status, current_step, "
    "thread_id, result, owl_name, channel, creation_ceiling, task_envelope, "
    "parent_task_id, parent_owl, delegate_key, lease_owner, superseded, "
    "created_at, updated_at, session_key"
)

# Minimal fields for checkpoint read — avoids pulling the full task row when
# only the blob is needed (future optimisation hook; currently the full row is
# fetched and the checkpoint_blob column is read off it).
_CHECKPOINT_BLOB_FIELD = "checkpoint_blob"


# =============================================================================
# THE ONE LOOP (migration 0119) — Bakir's loop+graph architecture, 2026-08-17.
#
# "whatever triggering in the platform, it's a task which should be part of the
# loop and be delivered."
#
# These methods are the durable half. The loop that drives them lives in
# pipeline/durable/loop.py; keeping the claim semantics here means they can be
# proven without a running event loop, and means there is exactly ONE place that
# knows how a task moves between states.
#
# WHY EXTEND `tasks` RATHER THAN ADD A TABLE. The tree already carried FOUR
# overlapping work engines. CLAUDE.md's rule — earned, not aspirational — is to
# find the existing loop and extend it. `tasks` was chosen because it already owns
# leasing, checkpoints, parent/child links and cost.
# =============================================================================

#: How long a claim is good for before another worker may take the row. Long
#: enough that a slow-but-live task is not stolen mid-flight; short enough that a
#: crashed worker's row comes back quickly.
DEFAULT_LEASE_SECONDS = 900

#: Backoff after a failure, in seconds, indexed by attempt. Capped rather than
#: unbounded-exponential: at 30 attempts a doubling schedule would put the last
#: retry weeks out, which for the user is indistinguishable from "it gave up".
_BACKOFF_SECONDS = (5, 15, 60, 300, 900)

#: Fallback when no settings are wired. The REAL list is
#: ``settings.task_loop.permanent_failure_classes`` — which failures are truly
#: permanent is deployment-specific (what is unrecoverable behind one gateway is a
#: transient blip behind another), so it must not be a constant compiled in here.
_PERMANENT_CLASSES_FALLBACK = frozenset({"permanent", "auth", "not_found", "refused"})


def _permanent_classes() -> frozenset[str]:
    """The configured permanent-failure classes, or the fallback.

    Read at call time, not import time, so a settings change takes effect without
    a redeploy — and never raises: an unreadable config degrades to the fallback
    rather than making every failure look retryable.
    """
    try:
        from stackowl.pipeline.services import get_services

        cfg = getattr(get_services(), "settings", None)
        if cfg is not None:
            return frozenset(cfg.task_loop.permanent_failure_classes)
    except Exception as exc:
        log.tasks.warning(
            "[loop] could not read permanent_failure_classes — using the fallback",
            exc_info=exc,
        )
    return _PERMANENT_CLASSES_FALLBACK


def _backoff_for(attempt: int) -> int:
    idx = min(max(attempt - 1, 0), len(_BACKOFF_SECONDS) - 1)
    return _BACKOFF_SECONDS[idx]


def _channel_of(destination: str | None) -> str | None:
    """"telegram:72055773" -> "telegram". None when there is no destination."""
    if not destination:
        return None
    return destination.split(":", 1)[0] or None


def _address_of(destination: str | None) -> str | None:
    """"telegram:72055773" -> "72055773". None for a channel-only destination
    like "cli", which addresses its single terminal implicitly."""
    if not destination or ":" not in destination:
        return None
    return destination.split(":", 1)[1] or None


def _split(raw: Any) -> tuple[str, ...]:
    """Parse a stored comma-list. Total: bad data reads as empty, never raises."""
    if not raw:
        return ()
    try:
        return tuple(p for p in str(raw).split(",") if p)
    except Exception:  # pragma: no cover — defensive
        return ()


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
            "session_key": task.session_key,
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

    async def create_child_task(
        self,
        *,
        child_task_id: str,
        parent_task_id: str,
        parent_owl: str,
        delegate_key: str,
        goal: str,
        owl_name: str,
        channel: str,
    ) -> DurableTask:
        """Claim-or-create a delegated child task row, then return it (D1 §7.1).

        ``INSERT ... ON CONFLICT(owner_id, task_id) DO NOTHING`` so two racers
        (a live parent + startup recovery) deriving the same deterministic id
        produce exactly ONE row — the loser's INSERT is a no-op. Both callers
        then re-``get`` the SAME record. This is distinct from the root-task
        INSERT (a duplicate root id IS a bug we want surfaced); never reuse
        :meth:`create` for children.
        """
        # 1. ENTRY
        log.tasks.debug(
            "[tasks] store.create_child_task: entry",
            extra={"_fields": {
                "child_task_id": child_task_id, "parent_task_id": parent_task_id,
                "owner_id": self._owner_id, "parent_owl": parent_owl,
            }},
        )
        now = datetime.now(tz=UTC).isoformat()
        sql = (
            "INSERT INTO tasks "  # noqa: S608 — columns are literals
            "(task_id, owner_id, goal, status, current_step, parent_task_id, "
            "parent_owl, delegate_key, owl_name, channel, superseded, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, 'running', 0, ?, ?, ?, ?, ?, 0, ?, ?) "
            "ON CONFLICT(owner_id, task_id) DO NOTHING"
        )
        params = [
            child_task_id, self._owner_id, goal, parent_task_id, parent_owl,
            delegate_key, owl_name, channel, now, now,
        ]
        # 2. DECISION — DO NOTHING means a row already exists; either way re-SELECT.
        affected = await self._db.execute_returning_rowcount(sql, params)
        # 3. STEP — read back the canonical record (winner's or pre-existing).
        record = await self.get(child_task_id)
        # 4. EXIT
        log.tasks.info(
            "[tasks] store.create_child_task: exit",
            extra={"_fields": {
                "child_task_id": child_task_id, "created": affected == 1,
                "owner_id": self._owner_id,
            }},
        )
        return record

    async def claim_child_lease(self, task_id: str, *, lease_owner: str) -> bool:
        """Atomically claim the single-owner execution lease for a child (D1 §7.1).

        CAS: ``UPDATE tasks SET lease_owner=? WHERE owner_id=? AND task_id=? AND
        lease_owner IS NULL``. Returns True iff THIS call won (rows-affected == 1).
        The winner executes the child; a loser polls the durable record. Mirrors
        :meth:`claim_for_recovery`'s direct-SQL CAS bypass.
        """
        # 1. ENTRY
        log.tasks.debug(
            "[tasks] store.claim_child_lease: entry",
            extra={"_fields": {
                "task_id": task_id, "owner_id": self._owner_id, "lease_owner": lease_owner,
            }},
        )
        sql = (
            f"UPDATE {self._table} SET lease_owner = ?, updated_at = ? "  # noqa: S608 — table from class
            "WHERE owner_id = ? AND task_id = ? AND lease_owner IS NULL"
        )
        params = [
            lease_owner, datetime.now(tz=UTC).isoformat(), self._owner_id, task_id,
        ]
        # 3. STEP — atomic CAS; rows-affected reveals the race winner.
        affected = await self._db.execute_returning_rowcount(sql, params)
        claimed = affected == 1
        # 4. EXIT
        log.tasks.info(
            "[tasks] store.claim_child_lease: exit",
            extra={"_fields": {
                "task_id": task_id, "claimed": claimed, "rows_affected": affected,
            }},
        )
        return claimed

    async def terminalize_child(
        self, task_id: str, status: TaskStatus, *, result: str | None = None,
    ) -> None:
        """Stamp a child task terminal as a projection of the parent's commit (D1 §7.2).

        The child's terminal status is written by the PARENT when it commits its
        delegate_task ledger entry — not by the child about itself. Thin wrapper
        over the owner-scoped status UPDATE so the call-site reads intentionally.
        """
        # 1. ENTRY
        log.tasks.debug(
            "[tasks] store.terminalize_child: entry",
            extra={"_fields": {
                "task_id": task_id, "owner_id": self._owner_id, "status": status,
            }},
        )
        await self.update_status(task_id, status, result=result)
        # 4. EXIT
        log.tasks.info(
            "[tasks] store.terminalize_child: exit",
            extra={"_fields": {"task_id": task_id, "status": status}},
        )

    async def supersede_child(self, task_id: str) -> None:
        """Tombstone a timed-out child so a slow eventual commit is neutralized (D1 §9).

        Sets ``superseded = 1`` via an owner-scoped UPDATE when the parent ABANDONS
        a timed-out child and advances a ladder rung. A slow child's late commit is
        thereby neutralized at the decision layer (defensive). Mirrors the
        owner-scoped UPDATE pattern (:meth:`update_status` / :meth:`save_checkpoint`)
        — the WHERE carries ``owner_id`` so a row owned by another principal can
        never be touched.
        """
        log.tasks.debug(
            "[tasks] store.supersede_child: entry",
            extra={"_fields": {"task_id": task_id, "owner_id": self._owner_id}},
        )
        sql = (
            f"UPDATE {self._table} SET superseded = 1, updated_at = ? "  # noqa: S608 — table from class
            "WHERE owner_id = ? AND task_id = ?"
        )
        await self._execute_owned(
            sql, [datetime.now(tz=UTC).isoformat(), self._owner_id, task_id]
        )
        log.tasks.info(
            "[tasks] store.supersede_child: superseded",
            extra={"_fields": {"task_id": task_id, "owner_id": self._owner_id}},
        )

    async def list_children(self, parent_task_id: str) -> builtins.list[DurableTask]:
        """All child tasks of ``parent_task_id`` for the bound owner (D1 §7)."""
        log.tasks.debug(
            "[tasks] store.list_children: entry",
            extra={"_fields": {"parent_task_id": parent_task_id, "owner_id": self._owner_id}},
        )
        rows = await self._fetch_owned(
            self._table, "parent_task_id = ?", (parent_task_id,)
        )
        kids = [_row_to_task(r) for r in rows]
        log.tasks.debug(
            "[tasks] store.list_children: exit",
            extra={"_fields": {"parent_task_id": parent_task_id, "count": len(kids)}},
        )
        return kids

    async def list_zombie_children(self) -> builtins.list[DurableTask]:
        """Running/recovering children whose parent is already terminal (D1 §7.3).

        These are unreachable by transitive resolution (the parent will never
        re-delegate), so the reaper marks them failed/abandoned. Owner-scoped
        self-join on the tasks table.
        """
        log.tasks.debug(
            "[tasks] store.list_zombie_children: entry",
            extra={"_fields": {"owner_id": self._owner_id}},
        )
        sql = (
            "SELECT child.* FROM tasks child "  # noqa: S608 — literals only
            "JOIN tasks parent "
            "ON parent.owner_id = child.owner_id "
            "AND parent.task_id = child.parent_task_id "
            "WHERE child.owner_id = ? "
            "AND child.parent_task_id IS NOT NULL "
            "AND child.status IN ('running', 'recovering') "
            "AND parent.status IN ('completed', 'failed')"
        )
        rows = await self._db.fetch_all(sql, (self._owner_id,))
        zombies = [_row_to_task(r) for r in rows]
        log.tasks.info(
            "[tasks] store.list_zombie_children: exit",
            extra={"_fields": {"owner_id": self._owner_id, "count": len(zombies)}},
        )
        return zombies

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


    async def get_accumulated_cost(self, task_id: str) -> float:
        """Return the cumulative USD spend recorded across ALL attempts (F093).

        Owner-scoped read off the ``accumulated_cost_usd`` column. A task missing
        for the bound owner (or a legacy row predating migration 0060) returns
        ``0.0`` — no prior spend, so a resume seeds its governor with 0.0 and
        behaves exactly as a first attempt.
        """
        log.tasks.debug(
            "[tasks] store.get_accumulated_cost: entry",
            extra={"_fields": {"task_id": task_id, "owner_id": self._owner_id}},
        )
        rows = await self._fetch_owned(self._table, "task_id = ?", (task_id,))
        if not rows:
            return 0.0
        raw = rows[0].get("accumulated_cost_usd")
        value = 0.0 if raw is None else float(raw)
        log.tasks.debug(
            "[tasks] store.get_accumulated_cost: exit",
            extra={"_fields": {"task_id": task_id, "accumulated_cost_usd": value}},
        )
        return value

    async def set_accumulated_cost(self, task_id: str, cost_usd: float) -> None:
        """Persist the cumulative USD spend for ``task_id`` (owner-scoped, F093).

        The durable executor passes the governor's ABSOLUTE current cumulative
        spend (prior attempts + this attempt) so the value is monotonic and
        idempotent across re-runs of the same iteration — never an additive delta
        that could double-count on replay. Fails loud on a missing/wrong-owner row
        (mirrors :meth:`save_checkpoint`) so a "durable" cost write can't silently
        no-op. Negative inputs are floored at 0.0.
        """
        safe = max(0.0, float(cost_usd))
        log.tasks.debug(
            "[tasks] store.set_accumulated_cost: entry",
            extra={"_fields": {
                "task_id": task_id, "owner_id": self._owner_id, "cost_usd": safe,
            }},
        )
        sql = (
            f"UPDATE {self._table} SET accumulated_cost_usd = ? "  # noqa: S608 — table from class
            "WHERE owner_id = ? AND task_id = ?"
        )
        await self._require_owned(task_id, op="set_accumulated_cost")
        await self._execute_owned(sql, [safe, self._owner_id, task_id])
        log.tasks.info(
            "[tasks] store.set_accumulated_cost: saved",
            extra={"_fields": {"task_id": task_id, "accumulated_cost_usd": safe}},
        )


    # ---- the ONE loop ---------------------------------------------------

    async def enqueue(self, task: DurableTask) -> None:
        """Write a task the loop may pick up. The universal ingress.

        Bakir: "whatever triggering in the platform, it's a task." A chat message,
        a schedule firing, an agent's own sub-goal — all become a row here, and the
        loop is what runs them. Stamps this store's principal so a caller states
        what the work MEANS, not which tenant it belongs to.
        """
        row = task.model_copy(update={"owner_id": self._owner_id})
        await self.create(row)
        await self._db.execute(
            f"UPDATE {self._table} SET destination=?, achievement=?, max_attempts=?, "  # noqa: S608
            "depends_on=?, trigger_kind=?, idempotency_key=? "
            "WHERE task_id=? AND owner_id=?",
            (task.destination, task.achievement, int(task.max_attempts),
             ",".join(task.depends_on) or None, task.trigger_kind,
             task.idempotency_key, task.task_id, self._owner_id),
        )
        log.tasks.info(
            "[loop] task enqueued",
            extra={"_fields": {"task_id": task.task_id, "trigger": task.trigger_kind,
                               "destination": task.destination,
                               "depends_on": list(task.depends_on)}},
        )

    async def claimable(
        self, *, limit: int = 10, now: datetime | None = None
    ) -> builtins.list[DurableTask]:
        """Rows the loop may run RIGHT NOW, newest-blocking-rules applied.

        A row qualifies when it is ``pending``, not superseded, its backoff has
        elapsed, and EVERY id in ``depends_on`` has been delivered. The dependency
        check is what makes the graph real: a parent that ran before its child
        would be answering with information it does not have yet.

        Deliberately returns them UNORDERED beyond the query's own ordering —
        Bakir: "five pending, five loops parallel... there's no ordering."
        """
        stamp = (now or datetime.now(UTC)).isoformat()
        rows = await self._db.fetch_all(
            f"SELECT {_SELECT_FIELDS}, destination, achievement, delivered_at, "  # noqa: S608
            "attempt_count, max_attempts, last_error, last_failure_class, "
            "banned_capabilities, next_attempt_at, lease_expires_at, depends_on, "
            "trigger_kind, idempotency_key "
            f"FROM {self._table} WHERE owner_id = ? AND status = 'pending' "
            "AND COALESCE(superseded, 0) = 0 "
            "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
            "ORDER BY created_at LIMIT ?",
            (self._owner_id, stamp, int(limit)),
        )
        out: builtins.list[DurableTask] = []
        for r in rows:
            deps = _split(r.get("depends_on"))
            if deps and not await self._deps_satisfied(r["task_id"], deps):
                continue
            out.append(_row_to_task(r))
        return out

    async def _deps_satisfied(self, task_id: str, deps: tuple[str, ...]) -> bool:
        """True when every dependency DELIVERED. A dead-lettered dependency
        dead-letters this row too, rather than leaving it blocked forever — a
        parent waiting on work that will never land is leaked work wearing a
        different hat, and leaked work is invisible."""
        marks = ",".join("?" for _ in deps)
        rows = await self._db.fetch_all(
            f"SELECT task_id, status FROM {self._table} "  # noqa: S608
            f"WHERE owner_id = ? AND task_id IN ({marks})",
            (self._owner_id, *deps),
        )
        by_id = {str(r["task_id"]): str(r["status"]) for r in rows}
        if any(by_id.get(d) in ("dead_letter", "failed") for d in deps):
            await self._db.execute(
                f"UPDATE {self._table} SET status='dead_letter', "  # noqa: S608
                "last_error=?, last_failure_class='dependency_failed', "
                "updated_at=? WHERE task_id=? AND owner_id=?",
                (f"a dependency will never land: "
                 f"{','.join(d for d in deps if by_id.get(d) in ('dead_letter','failed'))}",
                 datetime.now(UTC).isoformat(), task_id, self._owner_id),
            )
            log.tasks.warning(
                "[loop] task dead-lettered — a dependency failed permanently",
                extra={"_fields": {"task_id": task_id, "depends_on": list(deps)}},
            )
            return False
        return all(by_id.get(d) == "completed" for d in deps)

    async def claim(
        self, task_id: str, *, worker: str, lease_seconds: int = DEFAULT_LEASE_SECONDS
    ) -> bool:
        """Take the row for THIS worker. Returns whether this caller won.

        A compare-and-set — ``WHERE status='pending'`` — so under concurrency
        exactly one caller can win, which is what makes parallel workers safe. The
        same shape scheduler.py already runs in production for jobs. A SELECT
        followed by an UPDATE would double-run, and double-running a task that
        sends a message sends it twice.
        """
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=lease_seconds)
        affected = await self._db.execute_returning_rowcount(
            f"UPDATE {self._table} SET status='running', lease_owner=?, "  # noqa: S608
            "lease_expires_at=?, updated_at=? "
            "WHERE task_id=? AND owner_id=? AND status='pending'",
            (worker, expires.isoformat(), now.isoformat(), task_id, self._owner_id),
        )
        won = affected == 1
        log.tasks.info(
            "[loop] claim",
            extra={"_fields": {"task_id": task_id, "worker": worker, "won": won}},
        )
        return won

    async def reclaim_expired(self, *, now: datetime | None = None) -> int:
        """Return rows whose worker died to ``pending``. The crash-safety net.

        Without this a worker that dies mid-task leaves the row ``running``
        forever: no loop would ever pick it up again, and the work would be lost
        with nothing reporting it. Counts the attempt — a task that reliably kills
        its worker must still reach the ceiling rather than cycle for ever.
        """
        stamp = (now or datetime.now(UTC)).isoformat()
        affected = await self._db.execute_returning_rowcount(
            f"UPDATE {self._table} SET status='pending', lease_owner=NULL, "  # noqa: S608
            "lease_expires_at=NULL, attempt_count=COALESCE(attempt_count,0)+1, "
            "last_error='worker lease expired (crash or hang)', "
            "last_failure_class='lease_expired', updated_at=? "
            "WHERE owner_id=? AND status='running' "
            "AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?",
            (stamp, self._owner_id, stamp),
        )
        if affected:
            log.tasks.warning(
                "[loop] reclaimed tasks whose worker never came back",
                extra={"_fields": {"reclaimed": affected}},
            )
        return int(affected)

    async def fail_and_requeue(
        self, task_id: str, *, error: str, failure_class: str = "",
        banned: tuple[str, ...] = (),
    ) -> str:
        """Record the failure ON the row and put it back — or stop for good.

        Bakir: "if it fails, again moving back to pending and adding previous
        failure or action details. So next loop when it picks it, it also looks: is
        any previous one? Yes — learn from that experience."

        The learning is STRUCTURED, not narrated. ``banned_capabilities``
        ACCUMULATES so attempt three knows what one and two burned; pasting error
        prose forward instead would grow without bound and drown the goal by
        attempt ten.

        Returns the new status. Stops at the ceiling, or immediately for a failure
        class that cannot improve by being repeated.
        """
        row = await self.get(task_id)
        attempts = row.attempt_count + 1
        merged = tuple(sorted(set(row.banned_capabilities) | set(banned)))
        permanent = failure_class in _permanent_classes()
        exhausted = attempts >= row.max_attempts
        now = datetime.now(UTC)

        if permanent or exhausted:
            await self._db.execute(
                f"UPDATE {self._table} SET status='dead_letter', attempt_count=?, "  # noqa: S608
                "last_error=?, last_failure_class=?, banned_capabilities=?, "
                "lease_owner=NULL, lease_expires_at=NULL, updated_at=? "
                "WHERE task_id=? AND owner_id=?",
                (attempts, error[:2000], failure_class or None,
                 ",".join(merged) or None, now.isoformat(), task_id, self._owner_id),
            )
            log.tasks.error(
                "[loop] task DEAD-LETTERED — it will not be retried",
                extra={"_fields": {"task_id": task_id, "attempts": attempts,
                                   "max_attempts": row.max_attempts,
                                   "failure_class": failure_class,
                                   "reason": "permanent" if permanent else "ceiling",
                                   "error": error[:200]}},
            )
            await self._escalate(row, attempts=attempts, error=error,
                                 permanent=permanent)
            return "dead_letter"

        retry_at = now + timedelta(seconds=_backoff_for(attempts))
        await self._db.execute(
            f"UPDATE {self._table} SET status='pending', attempt_count=?, "  # noqa: S608
            "last_error=?, last_failure_class=?, banned_capabilities=?, "
            "next_attempt_at=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? "
            "WHERE task_id=? AND owner_id=?",
            (attempts, error[:2000], failure_class or None, ",".join(merged) or None,
             retry_at.isoformat(), now.isoformat(), task_id, self._owner_id),
        )
        log.tasks.info(
            "[loop] task requeued with what failed — the next attempt is constrained",
            extra={"_fields": {"task_id": task_id, "attempt": attempts,
                               "max_attempts": row.max_attempts,
                               "failure_class": failure_class,
                               "banned": list(merged),
                               "retry_at": retry_at.isoformat()}},
        )
        return "pending"

    async def _escalate(
        self, row: DurableTask, *, attempts: int, error: str, permanent: bool
    ) -> None:
        """TELL the operator a task stopped for good.

        This method exists because the design claimed dead letters were "visible
        and escalated" when they were only LOGGED. A log line in a file nobody is
        tailing is not an escalation — it is the silent give-up this whole loop was
        built to prevent, wearing the word "visible".

        Routed through the EXISTING ProactiveDeliverer rather than a new notifier:
        it already owns the transport, the undelivered outbox for when the channel
        is down, and (since ESC-19/ESC-20) recording what was said and styling it.
        A second send path would be the duplication CLAUDE.md forbids.

        Never raises and never changes the dead-letter outcome. The task has
        already stopped; failing to announce it must not also crash the caller —
        but the failure to announce is itself logged, because an escalation that
        silently fails is the original bug again one level up.
        """
        try:
            from stackowl.pipeline.services import get_services

            svc = get_services()
            cfg = getattr(svc, "settings", None)
            if cfg is not None and not cfg.task_loop.escalate_dead_letters:
                return
            deliverer = getattr(svc, "proactive_deliverer", None)
            if deliverer is None:
                log.tasks.warning(
                    "[loop] dead letter NOT escalated — no deliverer wired; the "
                    "task stopped and only this log says so",
                    extra={"_fields": {"task_id": row.task_id}},
                )
                return
            from stackowl.notifications.router import Notification

            why = "it cannot succeed by retrying" if permanent else (
                f"it used all {row.max_attempts} attempts"
            )
            target = _address_of(row.destination)
            await deliverer.deliver(Notification(
                message=(
                    f"I stopped working on: {row.goal[:200]}\n\n"
                    f"Why: {why}. Last failure: {error[:300]}\n"
                    f"Task {row.task_id} is kept as a dead letter, so nothing is "
                    f"lost — tell me to retry it and I will."
                ),
                urgency="normal",
                category="task_dead_letter",
                channel_name=_channel_of(row.destination) or row.channel,
                target=target,
                job_id=row.task_id,
            ))
            log.tasks.info(
                "[loop] dead letter escalated to the operator",
                extra={"_fields": {"task_id": row.task_id, "attempts": attempts}},
            )
        except Exception as exc:
            log.tasks.error(
                "[loop] could not escalate a dead letter — the task is stopped and "
                "the operator has NOT been told",
                exc_info=exc, extra={"_fields": {"task_id": row.task_id}},
            )

    async def mark_delivered(self, task_id: str, *, result: str) -> None:
        """The ONLY way a task completes. Bakir: "if it's delivered to me, it means
        loop is completed."

        ``delivered_at`` is the proof. A row that reached ``completed`` without it
        would be a self-report — the same overclaim shape this platform already
        pays for when a tool asserts success it never observed.
        """
        now = datetime.now(UTC)
        await self._db.execute(
            f"UPDATE {self._table} SET status='completed', result=?, "  # noqa: S608
            "delivered_at=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? "
            "WHERE task_id=? AND owner_id=?",
            (result, now.isoformat(), now.isoformat(), task_id, self._owner_id),
        )
        log.tasks.info(
            "[loop] task COMPLETE — its outcome reached its destination",
            extra={"_fields": {"task_id": task_id, "delivered_at": now.isoformat()}},
        )

    async def prune_completed(self, *, older_than_days: int = 1) -> int:
        """Delete COMPLETED rows past the window. Bakir asked for this explicitly.

        Scoped to ``completed`` on purpose. A ``dead_letter`` is never pruned: it is
        the one record of work that failed permanently, and it is precisely what the
        operator needs to see. The per-turn learning corpus lives in
        ``task_outcomes``, a separate table this never touches, so pruning a
        delivered task costs no experience.
        """
        affected = await self._db.execute_returning_rowcount(
            f"DELETE FROM {self._table} WHERE owner_id=? AND status='completed' "  # noqa: S608
            f"AND updated_at < datetime('now', ?)",
            (self._owner_id, f"-{int(older_than_days)} day"),
        )
        if affected:
            log.tasks.info(
                "[loop] pruned delivered tasks",
                extra={"_fields": {"pruned": affected, "older_than_days": older_than_days}},
            )
        return int(affected)


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
    def _dt(key: str) -> Any:
        raw = row.get(key)
        return None if raw is None else datetime.fromisoformat(str(raw))

    return DurableTask(
        # ---- the ONE loop (0119). .get() throughout: every reader of this
        # function predates these columns, and several select a narrower field
        # list, so a missing key must mean "default", never KeyError.
        destination=(None if row.get("destination") is None
                     else str(row["destination"])),
        achievement=(None if row.get("achievement") is None
                     else str(row["achievement"])),
        delivered_at=_dt("delivered_at"),
        attempt_count=int(row.get("attempt_count") or 0),
        max_attempts=int(row.get("max_attempts") or DEFAULT_MAX_ATTEMPTS),
        last_error=(None if row.get("last_error") is None
                    else str(row["last_error"])),
        last_failure_class=(None if row.get("last_failure_class") is None
                            else str(row["last_failure_class"])),
        banned_capabilities=_split(row.get("banned_capabilities")),
        next_attempt_at=_dt("next_attempt_at"),
        lease_expires_at=_dt("lease_expires_at"),
        depends_on=_split(row.get("depends_on")),
        trigger_kind=(None if row.get("trigger_kind") is None
                      else str(row["trigger_kind"])),
        idempotency_key=(None if row.get("idempotency_key") is None
                         else str(row["idempotency_key"])),
        task_id=str(row["task_id"]),
        owner_id=str(row["owner_id"]),
        goal=str(row["goal"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        current_step=int(row["current_step"]),
        thread_id=None if raw_thread is None else str(raw_thread),
        result=None if raw_result is None else str(raw_result),
        owl_name=None if raw_owl is None else str(raw_owl),
        channel=None if raw_channel is None else str(raw_channel),
        session_key=(None if row.get("session_key") is None
                     else str(row["session_key"])),
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


#: Statuses that mean a task is still in flight. Everything else
#: (``completed``, ``failed``) is terminal and releases the lane.
#:
#: ``parked`` counts: a parked task is waiting on a human or an approval, which is
#: precisely the work a 4 AM sweep must not sever. It is suspended, not finished.
_ACTIVE_TASK_STATUSES: tuple[str, ...] = ("pending", "running", "recovering", "parked")


async def any_active_task_for_lane(db: DbPool, session_key: str) -> bool:
    """Does this conversation lane have a durable task still in flight?

    Invariant I4's second condition (Bakir's Q12). Returns a BOOLEAN, never row
    content.

    DELIBERATELY OWNER-AGNOSTIC, which is why this is a module function and not a
    method on the owner-scoped :class:`DurableTaskStore`. The caller is the
    background sweeper, which has no principal; and objectives and tasks are
    created under whichever owner happened to be in scope, while a lane's identity
    is the PERSON. An owner-scoped read would therefore match nothing and invariant
    I4 would be a silent no-op — the failure this whole item keeps finding.

    The LANE is the scope here: a row carrying ``session_key = <this lane>`` belongs
    to that conversation whoever owns it. An empty lane key matches nothing rather
    than everything, so a caller with no lane cannot accidentally collide with the
    NULL rows that legacy work carries.
    """
    if not session_key:
        return False
    placeholders = ",".join("?" for _ in _ACTIVE_TASK_STATUSES)
    rows = await db.fetch_all(
        f"SELECT 1 FROM tasks WHERE session_key = ? "
        f"AND status IN ({placeholders}) LIMIT 1",
        (session_key, *_ACTIVE_TASK_STATUSES),
    )
    busy = bool(rows)
    log.tasks.debug(
        "[tasks] any_active_task_for_lane: exit",
        extra={"_fields": {"session_key": session_key, "busy": busy}},
    )
    return busy
