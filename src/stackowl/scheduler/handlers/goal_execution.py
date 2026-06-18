"""GoalExecutionHandler — runs a natural-language goal through the pipeline.

Story 7.2 wires this handler into :class:`OrchestratorBackend` so a scheduled
job whose ``params['goal']`` carries a user intent (e.g. "Check the weather
and summarise") drives the standard 8-step pipeline as if the user had
typed the goal at the prompt.

The handler also persists a row in ``job_results`` for ``/agents log``,
and removes the job entirely when ``params['run_once']`` is set — that path
turns the scheduler into a fire-and-forget background runner for one-shot
agents.

Backward compatibility: when constructed without a backend (the Story 7.1
test surface), execute() degrades to a noop success — the legacy contract
``handler_name == "goal_execution"`` and ``result.success is True`` is
preserved.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool
    from stackowl.pipeline.backends.base import OrchestratorBackend


_INSERT_JOB_RESULT_SQL = (
    "INSERT INTO job_results (job_id, run_at, status, result_text, duration_ms) "
    "VALUES (?, ?, ?, ?, ?)"
)
_DELETE_JOB_SQL = "DELETE FROM jobs WHERE job_id = ?"


class GoalExecutionHandler(JobHandler):
    """Runs ``job.params['goal']`` through the pipeline and persists the result."""

    def __init__(
        self,
        backend: OrchestratorBackend | None = None,
        db: DbPool | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._backend = backend
        self._db = db
        # ``settings`` gates the durable-pipeline routing. When None (the Story
        # 7.1/7.2 test surface) or ``settings.durable.goals`` is False, goal
        # execution stays on the legacy ephemeral path — byte-for-byte unchanged.
        self._settings = settings

    @property
    def handler_name(self) -> str:
        return "goal_execution"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        goal = str(job.params.get("goal", "") or "")
        log.scheduler.debug(
            "[scheduler] goal_execution.execute: entry",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "goal_preview": goal[:50],
                    "has_backend": self._backend is not None,
                    "has_db": self._db is not None,
                    "run_once": bool(job.params.get("run_once")),
                }
            },
        )
        TestModeGuard.assert_not_test_mode("goal_execution.execute")
        t0 = time.monotonic()

        # 2. DECISION — legacy / noop path when no backend wired (Story 7.1 surface)
        if self._backend is None:
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.debug(
                "[scheduler] goal_execution.execute: no backend — legacy noop",
                extra={"_fields": {"job_id": job.job_id}},
            )
            log.scheduler.info(
                "[scheduler] goal_execution.execute: exit",
                extra={
                    "_fields": {
                        "job_id": job.job_id,
                        "success": True,
                        "duration_ms": duration_ms,
                    }
                },
            )
            return JobResult(
                job_id=job.job_id,
                success=True,
                output="goal_execution: noop",
                error=None,
                duration_ms=duration_ms,
            )

        # 2. DECISION — empty goal is a contract violation when a backend is wired.
        if not goal.strip():
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.warning(
                "[scheduler] goal_execution.execute: empty goal",
                extra={"_fields": {"job_id": job.job_id}},
            )
            await self._record_result(job.job_id, "failed", "empty goal", duration_ms)
            return JobResult(
                job_id=job.job_id,
                success=False,
                output=None,
                error="goal is empty — nothing to execute",
                duration_ms=duration_ms,
                metadata={"goal": goal[:100]},
            )

        # 3. STEP — build pipeline state and run
        trace_id = f"goal-{uuid.uuid4().hex[:8]}"
        session_id = f"goal-{job.job_id[:8]}"
        state = PipelineState(
            trace_id=trace_id,
            session_id=session_id,
            input_text=goal,
            channel="cli",
            owl_name="secretary",
            pipeline_step="",
            # Cron/scheduler goal execution has no user present to answer a
            # mid-turn clarify; default-deny so a clarify call never parks a
            # scheduler worker slot waiting for an answer that cannot come.
            interactive=False,
        )

        # 2. DECISION — durable routing. ONLY when settings.durable.goals is True
        #    AND a real DbPool is wired does the goal drive durably: a DurableTask
        #    is created, state.task_id is stamped (so the B2 execute step runs the
        #    drive checkpointed + exactly-once ledger-guarded), and the task is
        #    finalized completed/parked/failed. That whole task lifecycle is owned
        #    by DurableTaskRunner (shared with the B4 recovery/resume path). When
        #    the flag is False (default) OR no db is available, the pipeline runs
        #    the legacy ephemeral path — byte-for-byte unchanged below.
        durable = self._durable_enabled()
        log.scheduler.debug(
            "[scheduler] goal_execution.execute: pipeline submitted",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "durable": durable,
                }
            },
        )

        try:
            if durable:
                # DURABLE PATH — DurableTaskRunner owns create→drive→finalize so
                # this handler and the future B4 recovery path share ONE lifecycle
                # implementation (incl. the idempotent terminal-status guard).
                final_state = await self._run_durable(goal, state)
            else:
                # EPHEMERAL PATH — legacy, byte-for-byte unchanged.
                final_state = await self._backend.run(state)
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.error(
                "[scheduler] goal_execution.execute: pipeline raised",
                exc_info=exc,
                extra={
                    "_fields": {"job_id": job.job_id, "duration_ms": duration_ms}
                },
            )
            await self._record_result(
                job.job_id, "failed", f"pipeline error: {exc}", duration_ms
            )
            return JobResult(
                job_id=job.job_id,
                success=False,
                output=None,
                error=str(exc),
                duration_ms=duration_ms,
                metadata={"goal": goal[:100]},
            )

        duration_ms = (time.monotonic() - t0) * 1000
        response_text = "".join(c.content for c in final_state.responses)
        success = not bool(final_state.errors)
        # A durable PARK is NOT a plain failure: the side effect was refused
        # (replay-uncertain), the task awaits a resume. Surface it distinctly so
        # /agents log reads "parked awaiting input", never a bare "failed".
        parked = final_state.durable_parked
        log.scheduler.info(
            "[scheduler] goal_execution.execute: pipeline complete",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "response_len": len(response_text),
                    "errors": len(final_state.errors),
                    "durable_parked": parked,
                }
            },
        )

        # 3. STEP — persist row in job_results so /agents log can show history. A
        #    parked durable task is recorded distinctly (status "parked" + a clear
        #    blocker line) rather than a generic "failed".
        if parked:
            blocker = "; ".join(final_state.errors) or "durable replay uncertain"
            status = "parked"
            result_text: str | None = f"PARKED awaiting input — {blocker}"
        else:
            status = "completed" if success else "failed"
            result_text = response_text or (
                "; ".join(final_state.errors) if final_state.errors else None
            )
        await self._record_result(job.job_id, status, result_text, duration_ms)

        # 3. STEP — fire-and-forget agents delete themselves after a successful run.
        if success and bool(job.params.get("run_once")):
            await self._delete_job(job.job_id)

        # 4. EXIT — a parked durable task surfaces a distinct, unambiguous signal
        #    (output says PARKED + the blocker; metadata.parked True) so /agents
        #    log never shows a bare "failed" for work that is merely awaiting a
        #    resume. The ephemeral (flag-off) path never parks, so this branch is
        #    inert there and the legacy JobResult shape is preserved.
        log.scheduler.info(
            "[scheduler] goal_execution.execute: exit",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "success": success,
                    "parked": parked,
                    "duration_ms": duration_ms,
                }
            },
        )
        if parked:
            blocker = "; ".join(final_state.errors) or "durable replay uncertain"
            return JobResult(
                job_id=job.job_id,
                success=False,
                output=f"PARKED awaiting input — {blocker}",
                error=None,
                duration_ms=duration_ms,
                metadata={"goal": goal[:100], "parked": True, "blocker": blocker},
            )
        return JobResult(
            job_id=job.job_id,
            success=success,
            output=response_text or None,
            error="; ".join(final_state.errors) if final_state.errors else None,
            duration_ms=duration_ms,
            metadata={"goal": goal[:100]},
        )

    # ------------------------------------------------------------------ helpers

    def _durable_enabled(self) -> bool:
        """True iff durable goal routing is switched on AND a DbPool is wired.

        Hot-reload friendly: reads ``settings.durable.goals`` live on every call
        rather than caching, so an in-place settings swap takes effect on the next
        goal without re-wiring the handler.
        """
        if self._settings is None or self._db is None:
            return False
        return bool(self._settings.durable.goals)

    async def _run_durable(self, goal: str, state: PipelineState) -> PipelineState:
        """Drive ``goal`` through the durable lifecycle and return the final state.

        Delegates the WHOLE task lifecycle (create ``running`` task → stamp the
        durable scope on ``state`` → drive the pipeline → finalize
        completed/parked/failed via the idempotent terminal-status guard) to
        :class:`DurableTaskRunner`, so this handler and the future B4 recovery
        path share ONE lifecycle implementation. Only reached when
        :meth:`_durable_enabled` is True, so ``self._db`` is guaranteed wired.

        Fails LOUD: a create/finalize/backend error propagates to ``execute``'s
        handler (which records the failure + builds the failure JobResult) — a
        "durable" goal is never silently downgraded to a non-durable run.
        """
        assert self._db is not None  # narrowed by _durable_enabled()
        assert self._backend is not None  # execute() returns early when None
        from stackowl.pipeline.durable.store import DurableTaskStore
        from stackowl.pipeline.durable.task_runner import DurableTaskRunner
        from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

        # Owner: DEFAULT_PRINCIPAL_ID for now. Multi-tenant goals (per-user
        # assignment) thread a real owning principal in here later (FR13).
        log.scheduler.info(
            "[scheduler] goal_execution: durable routing ON — delegating to runner",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        store = DurableTaskStore(self._db, DEFAULT_PRINCIPAL_ID)
        runner = DurableTaskRunner(store, self._backend)
        final_state, task_id = await runner.run(goal=goal, state=state)
        log.scheduler.debug(
            "[scheduler] goal_execution: durable runner returned",
            extra={"_fields": {
                "task_id": task_id, "parked": final_state.durable_parked,
            }},
        )
        return final_state

    async def _record_result(
        self,
        job_id: str,
        status: str,
        result_text: str | None,
        duration_ms: float,
    ) -> None:
        """Insert a row into ``job_results`` — degrades to noop if db is None."""
        if self._db is None:
            log.scheduler.debug(
                "[scheduler] goal_execution._record_result: no db wired — skipping persist",
                extra={"_fields": {"job_id": job_id, "status": status}},
            )
            return
        run_at = datetime.now(UTC).isoformat()
        try:
            await self._db.execute(
                _INSERT_JOB_RESULT_SQL,
                (job_id, run_at, status, result_text, duration_ms),
            )
        except Exception as exc:  # B5 — never silent
            log.scheduler.warning(
                "[scheduler] goal_execution._record_result: insert failed",
                exc_info=exc,
                extra={"_fields": {"job_id": job_id, "status": status}},
            )
            return
        log.scheduler.debug(
            "[scheduler] goal_execution._record_result: written",
            extra={"_fields": {"job_id": job_id, "status": status, "run_at": run_at}},
        )

    async def _delete_job(self, job_id: str) -> None:
        """Remove a one-shot agent from the ``jobs`` table after a successful run."""
        if self._db is None:
            log.scheduler.debug(
                "[scheduler] goal_execution._delete_job: no db wired — skipping delete",
                extra={"_fields": {"job_id": job_id}},
            )
            return
        try:
            await self._db.execute(_DELETE_JOB_SQL, (job_id,))
        except Exception as exc:  # B5 — never silent
            log.scheduler.warning(
                "[scheduler] goal_execution._delete_job: delete failed",
                exc_info=exc,
                extra={"_fields": {"job_id": job_id}},
            )
            return
        log.scheduler.info(
            "[scheduler] goal_execution._delete_job: removed one-shot agent",
            extra={"_fields": {"job_id": job_id}},
        )
