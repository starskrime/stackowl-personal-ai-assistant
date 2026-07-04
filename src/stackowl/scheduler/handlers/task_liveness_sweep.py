"""TaskLivenessSweepHandler (Task 9) — periodic durable-task liveness watchdog.

B4 crash-recovery (:mod:`stackowl.pipeline.durable.recovery`) only reaps
orphaned ``running``/``recovering`` tasks at BOOT — it never runs again while
the server is alive. If a task's backing background drive dies mid-execution
(the drive coroutine is killed, wedges, or its process is otherwise lost)
while the rest of the server keeps running, that task's row stays stuck
``status='running'`` FOREVER: nothing notices until the next full restart.

This handler closes that gap: a recurring scheduler job scans for ``running``
ROOT tasks whose ``updated_at`` has gone stale (older than
:data:`DEFAULT_STALE_AFTER_S`) and reclaims each one through the EXACT SAME
claim(CAS)->reconstruct->background-resume unit boot recovery uses
(:meth:`~stackowl.pipeline.durable.recovery.DurableTaskRecoverer.reclaim_one`)
— never a second, divergent copy of crash-recovery logic.

Scope, deliberately narrow (see the CAS semantics in
``DurableTaskStore.claim_for_recovery`` and the roots-only comment in
``recovery.recover``):

* Only ``status='running'`` rows are considered stale-eligible. Unlike BOOT
  recovery (which also reaps ``recovering`` because the prior process is
  provably dead), this sweep runs INSIDE a live server where ``recovering``
  is a real, valid, transient state a live claim passes through in the few
  DB round-trips between ``claim_for_recovery`` and ``_return_to_running``.
  Treating a ``recovering`` row as stale here would race a legitimate live
  claim in progress — a different, and wrong, failure mode than the boot
  case this sweep is modeled on.
* Only ROOT tasks (``parent_task_id IS NULL``) are reclaimed directly — a
  delegated child is resumed TRANSITIVELY when its parent re-executes
  ``delegate_task``; reclaiming it here would double-drive it as a detached
  top-level goal (same rule ``recovery.recover`` already enforces).

Also exposed as a :class:`~stackowl.infra.resilience.HealableResource` /
:class:`~stackowl.health.status.HealthContributor` pair (same object, same
pattern the channel adapters use — see ``scheduler/assembly.py``'s
telegram/discord/slack wiring) so ``health_sweep`` can trigger an immediate
reclaim on demand via ``ensure_available()``, and so a pile of stale tasks
shows up as a `degraded` subsystem in the aggregate health picture.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from stackowl.health.status import HealthStatus
from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.pipeline.durable.recovery import DurableTaskRecoverer
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.db.pool import DbPool
    from stackowl.pipeline.backends.base import OrchestratorBackend
    from stackowl.pipeline.durable.task import DurableTask

#: How long a ``running`` root task may go without an ``updated_at`` bump
#: before this sweep treats it as orphaned mid-execution.
#:
#: No explicit "typical single ReAct-drive wall-clock duration" constant is
#: defined elsewhere in the codebase (checked: ``DEFAULT_TURN_MAX_STEPS``
#: bounds *iteration count*, not wall time; the sandbox's 30s ceiling bounds a
#: single TOOL call, not a whole drive). 10 minutes is chosen as a generous
#: upper bound on a live, healthy multi-iteration ReAct drive (each iteration
#: touches an LLM call plus tool I/O; several iterations comfortably fit
#: inside 10 minutes on this box) while still catching a genuinely dead drive
#: within a bounded window rather than leaving it stuck until the next
#: restart.
DEFAULT_STALE_AFTER_S = 600.0


class TaskLivenessSweepHandler(JobHandler):
    """Periodic sweep that reclaims durable tasks stuck ``running`` mid-flight.

    Constructed with the SAME ``db``/``backend`` the boot-time
    ``recover_durable_tasks`` uses, so the internal
    :class:`DurableTaskRecoverer` it holds performs an IDENTICAL reclaim to
    the boot path — just triggered on a recurring cadence instead of once at
    startup.
    """

    def __init__(
        self,
        db: DbPool,
        backend: OrchestratorBackend,
        *,
        owner_id: str = DEFAULT_PRINCIPAL_ID,
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
        clock: Clock | None = None,
    ) -> None:
        self._store = DurableTaskStore(db, owner_id=owner_id)
        self._recoverer = DurableTaskRecoverer(db, backend, owner_id=owner_id)
        self._owner_id = owner_id
        self._stale_after_s = stale_after_s
        self._clock = clock or WallClock()
        # HealableResource cache — refreshed by health_check()/ensure_available(),
        # mirrors every other implementer in this codebase (EmbeddingRegistry,
        # LanceDBAdapter, the telegram adapter): `available` is a bare cached
        # read, never a fresh probe (a probe needs an `await`, which the
        # HealableResource protocol's sync `available` property can't do).
        self._available = True
        self._unavailable_reason: str | None = None

    @property
    def handler_name(self) -> str:
        return "task_liveness_sweep"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        log.scheduler.debug(
            "[scheduler] task_liveness_sweep.execute: entry",
            extra={"_fields": {"job_id": job.job_id, "stale_after_s": self._stale_after_s}},
        )
        t0 = time.monotonic()
        try:
            stale = await self._find_stale()
        except Exception as exc:  # never let a query error wedge the scheduler
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.error(
                "[scheduler] task_liveness_sweep.execute: stale-query failed",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change",
                success=False,
                output=None,
                error=str(exc),
                duration_ms=duration_ms,
            )

        # 2. DECISION — nothing stale → quiet exit.
        if not stale:
            self._set_cache(available=True, reason=None)
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.debug(
                "[scheduler] task_liveness_sweep.execute: no stale tasks",
                extra={"_fields": {"job_id": job.job_id}},
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change",
                success=True,
                output="stale=0",
                error=None,
                duration_ms=duration_ms,
                metadata={"stale_found": 0, "reclaimed": 0},
            )

        # 3. STEP — reclaim each stale row through the SAME shared unit boot
        #    recovery uses. Fail-open per task (reclaim_one never raises).
        reclaimed = 0
        for task in stale:
            if await self._recoverer.reclaim_one(task):
                reclaimed += 1
        duration_ms = (time.monotonic() - t0) * 1000
        still_stale = len(stale) - reclaimed
        self._set_cache(
            available=still_stale == 0,
            reason=(
                None if still_stale == 0
                else f"{still_stale} stale running task(s) could not be reclaimed"
            ),
        )
        # 4. EXIT
        log.scheduler.warning(
            "[scheduler] task_liveness_sweep.execute: reclaimed stale running task(s)",
            extra={"_fields": {
                "job_id": job.job_id, "stale_found": len(stale), "reclaimed": reclaimed,
            }},
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change",
            success=True,
            output=f"stale={len(stale)} reclaimed={reclaimed}",
            error=None,
            duration_ms=duration_ms,
            metadata={"stale_found": len(stale), "reclaimed": reclaimed},
        )

    async def _find_stale(self) -> list[DurableTask]:
        """Root ``running`` tasks whose ``updated_at`` is older than the threshold."""
        running = await self._store.list(status="running")
        now = self._clock.now()
        stale = [
            t for t in running
            if t.parent_task_id is None
            and (now - t.updated_at).total_seconds() >= self._stale_after_s
        ]
        log.scheduler.debug(
            "[scheduler] task_liveness_sweep._find_stale: exit",
            extra={"_fields": {
                "owner_id": self._owner_id, "running": len(running), "stale": len(stale),
            }},
        )
        return stale

    def _set_cache(self, *, available: bool, reason: str | None) -> None:
        self._available = available
        self._unavailable_reason = reason

    # ----- HealthContributor protocol ------------------------------------
    @property
    def contributor_name(self) -> str:
        return "task_liveness"

    async def health_check(self) -> HealthStatus:
        t0 = time.monotonic()
        log.scheduler.debug("[scheduler] task_liveness_sweep.health_check: entry")
        try:
            stale = await self._find_stale()
        except Exception as exc:  # a probe error must never wedge the aggregator
            log.scheduler.error(
                "[scheduler] task_liveness_sweep.health_check: stale-query failed",
                exc_info=exc,
            )
            self._set_cache(available=False, reason=str(exc))
            return HealthStatus(
                name=self.contributor_name, status="down", message=str(exc),
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        self._set_cache(
            available=not stale,
            reason=None if not stale else f"{len(stale)} stale running task(s)",
        )
        latency_ms = (time.monotonic() - t0) * 1000
        log.scheduler.debug(
            "[scheduler] task_liveness_sweep.health_check: exit",
            extra={"_fields": {"stale": len(stale)}},
        )
        if not stale:
            return HealthStatus(
                name=self.contributor_name, status="ok", message=None, latency_ms=latency_ms
            )
        return HealthStatus(
            name=self.contributor_name, status="degraded",
            message=self._unavailable_reason, latency_ms=latency_ms,
        )

    # ----- HealableResource protocol --------------------------------------
    @property
    def available(self) -> bool:
        return self._available

    @property
    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason

    async def ensure_available(self) -> None:
        """Immediately reclaim every currently-stale task — no waiting for the
        next scheduled tick.

        Uses the SAME :meth:`DurableTaskRecoverer.reclaim_one` shared unit as
        :meth:`execute`. Never raises: a reclaim failure is logged inside
        ``reclaim_one`` (fail-open per task) and the row simply stays
        unavailable, caught by the next tick.
        """
        # 1. ENTRY
        log.scheduler.debug("[scheduler] task_liveness_sweep.ensure_available: entry")
        stale = await self._find_stale()
        # 2. DECISION — nothing to do
        if not stale:
            self._set_cache(available=True, reason=None)
            log.scheduler.debug(
                "[scheduler] task_liveness_sweep.ensure_available: exit — nothing stale"
            )
            return
        # 3. STEP — reclaim every stale row synchronously, right now.
        reclaimed = 0
        for task in stale:
            if await self._recoverer.reclaim_one(task):
                reclaimed += 1
        still_stale = await self._find_stale()
        self._set_cache(
            available=not still_stale,
            reason=(
                None if not still_stale
                else f"{len(still_stale)} stale running task(s) could not be reclaimed"
            ),
        )
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] task_liveness_sweep.ensure_available: exit — reclaimed",
            extra={"_fields": {
                "found": len(stale), "reclaimed": reclaimed, "still_stale": len(still_stale),
            }},
        )

    def register_on_recycled(self, cb: Callable[[], None]) -> None:
        """No-op: no downstream dependent caches a reference to this handler."""
        log.scheduler.debug(
            "[scheduler] task_liveness_sweep.register_on_recycled: no-op (no dependents)"
        )
