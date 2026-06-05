"""DurableTaskRunner — the single owner of one durable task's full lifecycle.

A durable goal has a fixed lifecycle: CREATE a ``running`` :class:`DurableTask`,
stamp its ``task_id`` onto the :class:`~stackowl.pipeline.state.PipelineState` so
the B2-aware execute step drives durably (checkpointed + exactly-once
ledger-guarded), run the pipeline, then FINALIZE the task to a terminal status
(``completed`` / ``parked`` / ``failed``).

Before this seam existed that lifecycle lived inline inside
:class:`~stackowl.scheduler.handlers.goal_execution.GoalExecutionHandler`. B4
(crash recovery) needs the SAME finalize semantics when it resumes an orphaned
task. Extracting the lifecycle here gives both paths ONE implementation:

* :meth:`run`     — the fresh-goal path (handler) — CREATE then drive then finalize.
* :meth:`resume`  — the recovery path (B4) — LOAD an existing task then drive then
  finalize. B4 populates the ``durable_resume_*`` fields on ``state``; this seam
  only provides the lifecycle wrapper so recovery finalizes through the same guard.

Terminal-status guard (idempotent finalize)
--------------------------------------------
:meth:`_finalize` reads the task's CURRENT status first and only writes a
terminal status when the task is still ``running``. A task already in a terminal
state (``completed`` / ``parked`` / ``failed``) is left untouched. This makes
finalize idempotent: a double-finalize — e.g. a B4 resume of a task another
worker already finalized, or a retried ``run`` — can never overwrite/corrupt an
already-terminal task. The drive is therefore safe to wrap more than once.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.pipeline.authz_compose import resolve_owl_bounds
from stackowl.pipeline.durable.task import DurableTask, TaskStatus
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.pipeline.backends.base import OrchestratorBackend
    from stackowl.pipeline.durable.store import DurableTaskStore

#: The only non-terminal status a finalize is allowed to transition AWAY from.
_RUNNING: TaskStatus = "running"


class DurableTaskRunner:
    """Owns the create/drive/finalize lifecycle of one durable task.

    Constructed with an owner-scoped :class:`DurableTaskStore` (so every task it
    creates/finalizes belongs to that store's principal) and the pipeline
    ``backend`` it drives the goal through.
    """

    def __init__(self, store: DurableTaskStore, backend: OrchestratorBackend) -> None:
        self._store = store
        self._backend = backend

    async def run(
        self, *, goal: str, state: PipelineState
    ) -> tuple[PipelineState, str]:
        """Create a fresh durable task, drive it, and finalize it.

        CREATE a ``running`` :class:`DurableTask` for ``goal``, evolve ``state``
        with the new ``task_id`` + owning principal (so the execute step drives
        durably), run the backend, then finalize through the terminal-status
        guard. On a backend error the task is finalized ``failed`` and the
        exception is re-raised (fail loud — the handler owns the JobResult).

        Returns ``(final_state, task_id)``.
        """
        owner_id = self._store.owner_id
        task_id = f"task-{uuid.uuid4().hex[:12]}"
        # 1. ENTRY
        log.tasks.info(
            "[tasks] runner.run: creating durable task",
            extra={"_fields": {
                "task_id": task_id, "owner_id": owner_id, "goal_preview": goal[:50],
            }},
        )
        now = datetime.now(tz=UTC)
        # E2-S2 — snapshot the acting owl's bounds as the resume-monotonicity
        # ceiling. Best-effort: no registry / unbounded owl → None (no clamp).
        creation_ceiling = resolve_owl_bounds(state.owl_name, get_services().owl_registry)
        # Persist the ORIGINATING owl/channel from the creating state so B4
        # crash-recovery reconstructs the real owl/channel instead of hardcoding
        # 'secretary'/'cli' (the latent wrong-owl bug). Empty strings are coerced
        # to None so recovery's NULL-fallback (legacy rows) and an explicitly
        # contextless drive behave identically.
        await self._store.create(
            DurableTask(
                task_id=task_id,
                owner_id=owner_id,
                goal=goal,
                status="running",
                owl_name=state.owl_name or None,
                channel=state.channel or None,
                creation_ceiling=creation_ceiling,
                created_at=now,
                updated_at=now,
            )
        )
        # 2. DECISION — stamp the durable scope so the B2 execute step drives durably.
        durable_state = state.evolve(
            task_id=task_id, durable_owner_id=owner_id, creation_ceiling=creation_ceiling,
        )
        return await self._drive(task_id, durable_state)

    async def resume(
        self, *, task_id: str, state: PipelineState
    ) -> tuple[PipelineState, str]:
        """Resume an EXISTING durable task (B4 recovery), then finalize it.

        LOAD the task (must exist — a resume of a missing task fails loud via the
        store), evolve ``state`` with its ``task_id`` + owning principal, then
        drive + finalize through the SAME terminal-status guard as :meth:`run`.
        B4 populates the ``durable_resume_*`` fields on ``state`` before calling;
        this seam only provides the lifecycle wrapper so recovery finalizes
        correctly (and the guard makes resuming an already-terminal task a no-op).

        Returns ``(final_state, task_id)``.
        """
        owner_id = self._store.owner_id
        # 1. ENTRY — load (fail loud if missing/cross-owner).
        log.tasks.info(
            "[tasks] runner.resume: loading durable task",
            extra={"_fields": {"task_id": task_id, "owner_id": owner_id}},
        )
        await self._store.get(task_id)
        # 2. DECISION — stamp the durable scope; B4 has already populated resume_*.
        durable_state = state.evolve(task_id=task_id, durable_owner_id=owner_id)
        return await self._drive(task_id, durable_state)

    # ------------------------------------------------------------------ internals

    async def _drive(
        self, task_id: str, state: PipelineState
    ) -> tuple[PipelineState, str]:
        """Run the backend for ``state`` and finalize ``task_id`` from the outcome.

        Shared by :meth:`run` and :meth:`resume`. A backend exception finalizes
        the task ``failed`` and re-raises; otherwise the task is finalized from
        the final state: ``parked`` (B2 marked ``durable_parked``), ``completed``
        (no errors), or ``failed`` (errors present).
        """
        # 3. STEP — drive the pipeline.
        log.tasks.debug(
            "[tasks] runner._drive: backend run",
            extra={"_fields": {"task_id": task_id, "trace_id": state.trace_id}},
        )
        try:
            final_state = await self._backend.run(state)
        except Exception as exc:
            # Fail loud: finalize failed (guarded) then re-raise so the handler
            # records the failure + builds the JobResult. Never swallow.
            log.tasks.error(
                "[tasks] runner._drive: backend raised — finalizing failed",
                exc_info=exc,
                extra={"_fields": {"task_id": task_id}},
            )
            await self._finalize(task_id, "failed", result=f"pipeline error: {exc}")
            raise

        # 2. DECISION — map the final state to a terminal status.
        if final_state.durable_parked:
            blocker = "; ".join(final_state.errors) or "durable replay uncertain"
            await self._finalize(task_id, "parked", result=blocker)
        elif final_state.errors:
            await self._finalize(
                task_id, "failed", result="; ".join(final_state.errors) or None,
            )
        else:
            response_text = "".join(c.content for c in final_state.responses)
            await self._finalize(task_id, "completed", result=response_text or None)
        # 4. EXIT
        log.tasks.info(
            "[tasks] runner._drive: exit",
            extra={"_fields": {
                "task_id": task_id, "parked": final_state.durable_parked,
                "errors": len(final_state.errors),
            }},
        )
        return final_state, task_id

    async def _finalize(
        self, task_id: str, status: TaskStatus, *, result: str | None
    ) -> None:
        """Move ``task_id`` to a terminal status — IDEMPOTENT via a status guard.

        Reads the task's CURRENT status first and only writes when it is still
        ``running``. A task already terminal (``completed`` / ``parked`` /
        ``failed``) is left untouched, so a double-finalize (B4 resume of an
        already-finalized task, or a retried run) can never corrupt it. Fails
        loud on a real store error (a task stuck ``running`` would leak a worker
        slot), so the update error propagates.
        """
        # 1. ENTRY — read current status (fail loud if the task vanished).
        current = await self._store.get(task_id)
        # 2. DECISION — terminal-status guard: only transition AWAY from running.
        if current.status != _RUNNING:
            log.tasks.info(
                "[tasks] runner._finalize: already terminal — skipping (idempotent)",
                extra={"_fields": {
                    "task_id": task_id, "current": current.status,
                    "requested": status,
                }},
            )
            return
        # 3. STEP — owner-scoped terminal write.
        log.tasks.debug(
            "[tasks] runner._finalize: writing terminal status",
            extra={"_fields": {"task_id": task_id, "status": status}},
        )
        await self._store.update_status(task_id, status, result=result)
        # 4. EXIT
        log.tasks.info(
            "[tasks] runner._finalize: finalized",
            extra={"_fields": {"task_id": task_id, "status": status}},
        )
