"""TS10 — single-flight per owl (ADR-T5 "single-flight lock per owl").

If an owl's previous scheduled run is still in progress when the next fires, the
new fire must be SKIPPED, never stacked. The scheduler already enforces this with
a compare-and-swap claim (F103): a job is dispatched only by winning the guarded
``pending -> running`` UPDATE. While a run is in flight the row is ``running``, so
(1) the poll SELECT (``WHERE status = 'pending'``) never re-selects it, and
(2) a direct ``_run_job`` loses the CAS claim and bails BEFORE the handler runs.

These tests assert the second guarantee on a REAL ``DbPool`` (the CAS reads
``changes()`` on the pool's single serialized connection), and the control case
that a genuinely pending job IS dispatched.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.base import HandlerRegistry, JobHandler, TriggerKind
from stackowl.scheduler.job import Job, JobResult
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.scheduler.scheduler_helpers import insert_job
from tests._story_7_2_helpers import make_job

pytestmark = pytest.mark.asyncio


class _RecordingHandler(JobHandler):
    """Counts how many times the scheduler actually dispatched it."""

    def __init__(self) -> None:
        self.executions = 0

    @property
    def handler_name(self) -> str:
        return "goal_execution"

    @property
    def trigger_kind(self) -> TriggerKind:
        return "on_demand"

    async def execute(self, job: Job) -> JobResult:
        self.executions += 1
        return JobResult(
            job_id=job.job_id, success=True, output="ok", error=None, duration_ms=1.0
        )


def _scheduler(db: DbPool, handler: _RecordingHandler) -> JobScheduler:
    registry = HandlerRegistry()
    registry.register(handler)
    return JobScheduler(db=db, handler_registry=registry)


async def test_in_flight_run_is_not_re_dispatched(tmp_db: DbPool) -> None:
    handler = _RecordingHandler()
    sched = _scheduler(tmp_db, handler)
    job = make_job()
    await insert_job(tmp_db, job)
    # Simulate the owl's previous run still IN PROGRESS (row already 'running').
    await tmp_db.execute(
        "UPDATE jobs SET status = 'running' WHERE job_id = ?", (job.job_id,)
    )

    # The next fire arrives: it must lose the CAS claim and skip — never stack.
    await sched._run_job(job)

    assert handler.executions == 0


async def test_pending_job_is_dispatched(tmp_db: DbPool) -> None:
    handler = _RecordingHandler()
    sched = _scheduler(tmp_db, handler)
    job = make_job()
    await insert_job(tmp_db, job)  # status 'pending' — free to run

    await sched._run_job(job)

    assert handler.executions == 1
