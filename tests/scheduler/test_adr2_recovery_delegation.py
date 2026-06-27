"""ADR-2 — the scheduler's retry-vs-fail DECISION delegates to the one RecoveryActuator.

The scheduler already has bounded retry recovery (STEER-5/F113: a separate ``retry_at``
slot, ``retry_count``, ``_MAX_RETRIES`` gate). ADR-2 unification routes the *decision*
("may this failed job be retried?") through the single ``RecoveryActuator.should_retry``
authority instead of an inline ``new_retries >= _MAX_RETRIES`` guard, so one policy governs
every subsystem. Byte-identical: a scheduled job failure is non-consequential and
transient-by-policy, so the authority agrees with the inline budget gate — the flag toggles
only WHERE the decision is made.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import stackowl.scheduler.scheduler as _sched_mod
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.scheduler.scheduler import JobScheduler

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def migrated_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "sched.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture(autouse=True)
def _reset_registry() -> object:
    HandlerRegistry.reset()
    yield None
    HandlerRegistry.reset()


class _FailingHandler(JobHandler):
    """Always fails — drives the retry-vs-fail decision."""

    def __init__(self) -> None:
        self.runs = 0

    @property
    def handler_name(self) -> str:
        return "goal_execution"

    async def execute(self, job: Job) -> JobResult:
        self.runs += 1
        return JobResult(
            job_id=job.job_id, success=False, output=None,
            error="transient", duration_ms=1.0,
        )


class _SpyActuator:
    """Records the Failures it was asked to classify; delegates to the real predicate."""

    def __init__(self) -> None:
        from stackowl.pipeline.recovery_actuator import RecoveryActuator

        self._real = RecoveryActuator()
        self.calls: list[object] = []

    def should_retry(self, failure: object) -> bool:
        self.calls.append(failure)
        return self._real.should_retry(failure)  # type: ignore[arg-type]


async def _seed_due(db: DbPool) -> str:
    sched = JobScheduler(db=db, tz="UTC")
    job = await sched.create_job(handler_name="goal_execution", schedule="daily@08:00")
    await db.execute(
        "UPDATE jobs SET next_run_at = ?, status = 'pending' WHERE job_id = ?",
        ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), job.job_id),
    )
    return job.job_id


async def test_retry_decision_routes_through_actuator_when_unify_on(
    migrated_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stackowl.pipeline.recovery_actuator import Failure

    monkeypatch.setattr(_sched_mod, "_unify_scheduler_enabled", lambda: True)
    HandlerRegistry.instance().register(_FailingHandler())
    job_id = await _seed_due(migrated_db)
    spy = _SpyActuator()

    sched = JobScheduler(db=migrated_db, tz="UTC", recovery=spy)  # type: ignore[arg-type]
    await sched._poll()  # fails (under budget) → retry

    # The authority was consulted with a typed scheduled-job Failure (delegation).
    assert len(spy.calls) == 1
    failure = spy.calls[0]
    assert isinstance(failure, Failure)
    assert failure.kind == "scheduled_job"
    assert failure.consequential is False
    # Byte-identical: retry slot set, count bumped — not terminally failed.
    row = (await migrated_db.fetch_all(
        "SELECT retry_at, retry_count, status FROM jobs WHERE job_id = ?", (job_id,),
    ))[0]
    assert row["retry_at"] is not None
    assert int(row["retry_count"]) == 1
    assert row["status"] == "pending"


async def test_retry_decision_inline_when_unify_off(
    migrated_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_sched_mod, "_unify_scheduler_enabled", lambda: False)
    HandlerRegistry.instance().register(_FailingHandler())
    job_id = await _seed_due(migrated_db)
    spy = _SpyActuator()

    sched = JobScheduler(db=migrated_db, tz="UTC", recovery=spy)  # type: ignore[arg-type]
    await sched._poll()

    assert spy.calls == []  # inline path — authority not consulted
    row = (await migrated_db.fetch_all(
        "SELECT retry_at, retry_count, status FROM jobs WHERE job_id = ?", (job_id,),
    ))[0]
    assert row["retry_at"] is not None and int(row["retry_count"]) == 1
