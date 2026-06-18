"""F103 — poller CAS claim (no double-dispatch) + delivery-attempt ledger.

The poller previously did an UNGUARDED ``UPDATE jobs SET status='running'`` after a
check-then-act SELECT, while ``run_now`` used the guarded CAS + ``_won_transition``.
So a poll tick racing ``run_now`` could BOTH dispatch the same occurrence. This
pins that the poller now wins-or-loses the same pending->running transition.

The delivery-attempt ledger (occurrence-scoped) closes the crash-replay delivery
gap: a ``dispatched``/``delivered`` row for (job_id, occurrence_key, channel)
suppresses a re-send on replay, WITHOUT collapsing the occurrence_key dedup.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.notifications.delivery_ledger import DeliveryLedger
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


class _CountingHandler(JobHandler):
    def __init__(self) -> None:
        self.runs: list[str] = []

    @property
    def handler_name(self) -> str:
        return "goal_execution"

    async def execute(self, job: Job) -> JobResult:
        self.runs.append(job.job_id)
        return JobResult(job_id=job.job_id, success=True, output="ok", error=None, duration_ms=1.0)


async def _seed_due_job(db: DbPool) -> str:
    sched = JobScheduler(db=db)
    job = await sched.create_job(handler_name="goal_execution", schedule="every 1m")
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    await db.execute(
        "UPDATE jobs SET next_run_at = ?, status = 'pending' WHERE job_id = ?",
        (past, job.job_id),
    )
    return job.job_id


# --------------------------------------------------------------- CAS double-dispatch


async def test_run_now_loses_while_poll_claim_inflight(migrated_db: DbPool) -> None:
    """While a poll's claim is in-flight (job 'running'), run_now must NOT dispatch.

    A handler that is still executing leaves the job in 'running'. A racing
    run_now reaches for the SAME pending->running CAS, loses (row not pending),
    and rejects — so the handler runs exactly once for that occurrence.
    """
    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    job_id = await _seed_due_job(migrated_db)

    # Simulate the poller having claimed the occurrence and still running it.
    await migrated_db.execute(
        "UPDATE jobs SET status = 'running' WHERE job_id = ?", (job_id,)
    )

    result = await JobScheduler(db=migrated_db).run_now(job_id)

    assert handler.runs == [], "run_now must lose the CAS while the poll claim is in-flight"
    assert result is not None and result.success is False, "run_now must reject, not silently no-op"


async def test_poll_skips_a_running_job(migrated_db: DbPool) -> None:
    """A job already in 'running' (claimed by another dispatcher) is not re-claimed."""
    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    job_id = await _seed_due_job(migrated_db)
    # Simulate another dispatcher having claimed it.
    await migrated_db.execute(
        "UPDATE jobs SET status = 'running' WHERE job_id = ?", (job_id,)
    )

    # Poll selects only status='pending' rows, so nothing runs.
    await JobScheduler(db=migrated_db)._poll()
    assert handler.runs == [], "a job claimed 'running' must not be dispatched by the poll"


# ----------------------------------------------- STEER-4/F110 recover vs poll race


async def test_recover_and_poll_dispatch_missed_job_exactly_once(migrated_db: DbPool) -> None:
    """A startup recover() racing the first _poll() on the SAME due replay-missed
    job dispatches the handler EXACTLY ONCE.

    F110 worried recover()'s replay (which calls _run_job) could double-fire with
    the poll loop. Both paths run the SAME pending->running CAS claim
    (_won_transition over the single serialized connection), so only one dispatcher
    wins the occurrence. This pins it with the REAL race (concurrent gather), not
    just a sequential check.
    """
    import asyncio

    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    job_id = await _seed_due_job(migrated_db)
    # The replay path only fires for replay_missed jobs inside the window.
    await migrated_db.execute(
        "UPDATE jobs SET replay_missed = 1 WHERE job_id = ?", (job_id,)
    )

    sched = JobScheduler(db=migrated_db)
    # Drive recover() and _poll() CONCURRENTLY against the same due occurrence.
    await asyncio.gather(sched.recover(), sched._poll())

    assert handler.runs == [job_id], (
        "the missed job must dispatch exactly once across a recover()/poll() race"
    )


# --------------------------------------------------------------- delivery ledger


async def test_ledger_pre_record_then_suppresses_replay(migrated_db: DbPool) -> None:
    ledger = DeliveryLedger(migrated_db)
    claimed = await ledger.claim_dispatch("j1", "k1@t1", "telegram")
    assert claimed is True, "first claim wins (pre-records 'dispatched')"

    # Replay: the same occurrence+channel is already dispatched -> suppressed.
    again = await ledger.claim_dispatch("j1", "k1@t1", "telegram")
    assert again is False, "a replay of the same occurrence+channel must be suppressed"


async def test_ledger_mark_delivered_then_still_suppressed(migrated_db: DbPool) -> None:
    ledger = DeliveryLedger(migrated_db)
    await ledger.claim_dispatch("j1", "k1@t1", "telegram")
    await ledger.mark("j1", "k1@t1", "telegram", "delivered")
    # A delivered row also suppresses a replay (already sent).
    assert await ledger.claim_dispatch("j1", "k1@t1", "telegram") is False


async def test_ledger_distinct_occurrence_not_suppressed(migrated_db: DbPool) -> None:
    """A different occurrence_key (next instant) is a NEW delivery, not deduped."""
    ledger = DeliveryLedger(migrated_db)
    await ledger.claim_dispatch("j1", "k1@t1", "telegram")
    assert await ledger.claim_dispatch("j1", "k1@t2", "telegram") is True


async def test_ledger_distinct_channel_not_suppressed(migrated_db: DbPool) -> None:
    """Same occurrence, different channel => independent delivery, allowed."""
    ledger = DeliveryLedger(migrated_db)
    await ledger.claim_dispatch("j1", "k1@t1", "telegram")
    assert await ledger.claim_dispatch("j1", "k1@t1", "slack") is True
