"""ObjectiveDriverHandler — advance standing objectives autonomously (1C).

The functional heart of the Objective Manager. A seeded scheduler job fires this
handler on a short cadence; each tick it advances every ACTIVE objective by its
next pending sub-goal, runs that sub-goal through the pipeline backend (durably
when ``durable.goals`` is on, else ephemerally — mirroring goal_execution),
records progress + an activity event, and decides:

* more pending sub-goals → keep going next tick (no notification — avoid spam);
* all sub-goals done → mark the objective ``done`` and notify the owner once;
* a sub-goal PARKS (a consequential/irreversible action it cannot get consent
  for in a non-interactive context) or FAILS → mark the objective ``blocked``
  and notify the owner. This is the act-on-reversible / ask-on-irreversible
  posture realized autonomously: the assistant works the reversible steps on its
  own and surfaces only the irreversible decision.

Delivery reuses the durable exactly-once seam (:class:`ProactiveJobDeliverer`)
by adapting the objective's own recipient columns into a synthetic delivery
``Job`` — the driver's seeded job has no per-objective recipient.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.objectives.model import Objective
from stackowl.objectives.store import ObjectiveStore
from stackowl.pipeline.state import PipelineState
from stackowl.scheduler.base import JobHandler, TriggerKind
from stackowl.scheduler.job import Job, JobResult
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool
    from stackowl.notifications.proactive_job import ProactiveJobDeliverer
    from stackowl.pipeline.backends.base import OrchestratorBackend

_HANDLER = "objective_driver"
_CATEGORY = "objective"


class ObjectiveDriverHandler(JobHandler):
    """Advance every active objective by one sub-goal per scheduler tick."""

    def __init__(
        self,
        db: DbPool | None,
        backend: OrchestratorBackend | None,
        *,
        settings: Settings | None = None,
        job_deliverer: ProactiveJobDeliverer | None = None,
        owner_id: str = DEFAULT_PRINCIPAL_ID,
    ) -> None:
        self._db = db
        self._backend = backend
        # Gates durable routing per sub-goal (read live for hot-reload), mirroring
        # GoalExecutionHandler — flag off (default) ⇒ legacy ephemeral path.
        self._settings = settings
        # The durable exactly-once delivery seam. None ⇒ no notification (back-
        # compat / unit surface); never a fake "delivered".
        self._job_deliverer = job_deliverer
        self._owner_id = owner_id

    @property
    def handler_name(self) -> str:
        return _HANDLER

    @property
    def trigger_kind(self) -> TriggerKind:
        # Seeded with a standing every-1m row in SchedulerAssembly, so the boot
        # wiring audit does not flag it as dangling.
        return "seeded"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        t0 = time.monotonic()
        log.scheduler.debug(
            "[scheduler] objective_driver.execute: entry",
            extra={"_fields": {"job_id": job.job_id, "has_db": self._db is not None}},
        )
        if self._db is None or self._backend is None:
            return JobResult(
                job_id=job.job_id, success=True,
                output="objective_driver: noop (no db/backend)", error=None,
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        store = ObjectiveStore(self._db, self._owner_id)
        active = await store.list_objectives(status="active")
        log.scheduler.debug(
            "[scheduler] objective_driver.execute: active objectives",
            extra={"_fields": {"count": len(active)}},
        )

        advanced = 0
        for objective in active:
            try:
                if await self._advance(store, objective):
                    advanced += 1
            except Exception as exc:  # noqa: BLE001 — one objective must not sink the tick
                log.scheduler.error(
                    "[scheduler] objective_driver.execute: objective advance failed",
                    exc_info=exc,
                    extra={"_fields": {"objective_id": objective.objective_id}},
                )

        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] objective_driver.execute: exit",
            extra={"_fields": {"advanced": advanced, "duration_ms": duration_ms}},
        )
        return JobResult(
            job_id=job.job_id, success=True,
            output=f"advanced {advanced} objective(s)", error=None,
            duration_ms=duration_ms, metadata={"advanced": advanced},
        )

    # ------------------------------------------------------------- internals

    async def _advance(self, store: ObjectiveStore, objective: Objective) -> bool:
        """Advance one objective by its next pending sub-goal. Returns did-work."""
        nxt = await store.next_pending_subgoal(objective.objective_id)
        if nxt is None:
            # All sub-goals finished — the objective is complete.
            await store.update_status(objective.objective_id, "done")
            await store.append_event(objective.objective_id, "completed", objective.intent)
            await self._notify(objective, f"✓ Objective complete: {objective.intent}")
            log.scheduler.info(
                "[scheduler] objective_driver: objective complete",
                extra={"_fields": {"objective_id": objective.objective_id}},
            )
            return True

        await store.update_subgoal(nxt.subgoal_id, "running")
        final_state, task_id = await self._run_subgoal(objective, nxt.description)
        response_text = "".join(c.content for c in final_state.responses)

        if final_state.durable_parked:
            blocker = "; ".join(final_state.errors) or "awaiting a decision"
            await store.update_subgoal(nxt.subgoal_id, "blocked", result=blocker, task_id=task_id)
            await store.update_status(objective.objective_id, "blocked", blocker=blocker)
            await store.append_event(objective.objective_id, "blocked", blocker)
            await self._notify(
                objective,
                f"⏸ Objective needs your decision: {objective.intent}\n{blocker}",
            )
            return True

        if final_state.errors:
            err = "; ".join(final_state.errors)
            await store.update_subgoal(nxt.subgoal_id, "failed", result=err, task_id=task_id)
            await store.update_status(objective.objective_id, "blocked", blocker=err)
            await store.append_event(objective.objective_id, "subgoal_failed", err)
            await self._notify(objective, f"⚠ Objective stalled: {objective.intent}\n{err}")
            return True

        await store.update_subgoal(nxt.subgoal_id, "done", result=response_text, task_id=task_id)
        await store.append_event(objective.objective_id, "subgoal_done", nxt.description)
        return True

    async def _run_subgoal(
        self, objective: Objective, description: str
    ) -> tuple[PipelineState, str | None]:
        """Run one sub-goal through the pipeline; returns (final_state, task_id)."""
        assert self._backend is not None  # narrowed by execute()
        trace_id = f"objgoal-{uuid.uuid4().hex[:8]}"
        state = PipelineState(
            trace_id=trace_id,
            session_id=f"objective-{objective.objective_id}",
            input_text=description,
            channel=objective.channel or "cli",
            owl_name="secretary",
            pipeline_step="",
            # No human present to answer a clarify; the handler owns delivery.
            interactive=False,
            defer_delivery=True,
        )
        if self._durable_enabled():
            from stackowl.pipeline.durable.store import DurableTaskStore
            from stackowl.pipeline.durable.task_runner import DurableTaskRunner

            assert self._db is not None  # narrowed by _durable_enabled
            store = DurableTaskStore(self._db, self._owner_id)
            runner = DurableTaskRunner(store, self._backend)
            final_state, task_id = await runner.run(goal=description, state=state)
            return final_state, task_id

        final_state = await self._backend.run(state)
        return final_state, None

    def _durable_enabled(self) -> bool:
        """True iff durable sub-goal routing is on AND a DbPool is wired."""
        if self._settings is None or self._db is None:
            return False
        return bool(self._settings.durable.goals)

    async def _notify(self, objective: Objective, message: str) -> None:
        """Deliver a progress/blocked message to the objective's owner, honestly."""
        if self._job_deliverer is None:
            log.scheduler.debug(
                "[scheduler] objective_driver._notify: no deliverer wired — skipping",
                extra={"_fields": {"objective_id": objective.objective_id}},
            )
            return
        synthetic = self._delivery_job(objective)
        try:
            outcome = await self._job_deliverer.deliver_for_job(
                synthetic, message=message, category=_CATEGORY, urgency="normal",
            )
            log.scheduler.info(
                "[scheduler] objective_driver._notify: delivered",
                extra={"_fields": {
                    "objective_id": objective.objective_id, "rollup": outcome.rollup,
                }},
            )
        except Exception as exc:  # noqa: BLE001 — a notify failure must not sink the tick
            log.scheduler.error(
                "[scheduler] objective_driver._notify: delivery raised",
                exc_info=exc,
                extra={"_fields": {"objective_id": objective.objective_id}},
            )

    @staticmethod
    def _delivery_job(objective: Objective) -> Job:
        """Adapt an objective's durable recipient into a synthetic delivery Job.

        The driver's seeded job has no per-objective recipient; DeliverySpec reads
        ``target_channels`` / ``target_addresses`` off a Job, so we carry the
        objective's own columns through a throwaway Job. A unique idempotency key
        makes each notification a distinct delivery-ledger occurrence.
        """
        now = datetime.now(tz=UTC).isoformat()
        return Job(
            job_id=f"objective-{objective.objective_id}",
            handler_name=_HANDLER,
            schedule="every 1m",
            idempotency_key=f"objective-{objective.objective_id}-{uuid.uuid4().hex[:8]}",
            last_run_at=None,
            next_run_at=now,
            status="running",
            primary_channel=objective.channel,
            target_channels=list(objective.target_channels),
            target_addresses=dict(objective.target_addresses),
        )
