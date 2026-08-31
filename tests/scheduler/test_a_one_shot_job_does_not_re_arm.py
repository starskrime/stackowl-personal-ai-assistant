"""A one-shot job must not come back tomorrow, and 92 of them are queued for it.

MEASURED 2026-08-31 on the live ``jobs`` table::

    schedule 'manual'   92 rows   ALL rollover_summary
                                  ALL status='pending', enabled=1
                                  ALL last_run_at 2026-08-31T09:0x
                                  ALL next_run_at 2026-09-01T09:0x

and in the log, 326 occurrences since 2026-08-28 of::

    [scheduler] compute_next_run: cron parse failed — defaulting to +1d
    {'schedule': 'manual'}

``rollover_summary`` summarises ONE conversation boundary that happened ONCE.
Ninety-two of them are armed to fire in a burst tomorrow morning and re-summarise
conversations that ended days ago.

THE GUARD EXISTS, IS DOCUMENTED, AND IS NOT ASKED. ``enqueue_rollover_summary``
sets the marker and says exactly what it is for::

    # The scheduler decides recurring-vs-one-shot from this flag
    # (scheduler._is_recurring). Without it a boundary's job would re-arm
    # onto a cadence for ever and re-summarise a conversation that ended once.
    "run_once": True,

``_is_recurring`` reads it correctly. ``_idempotent_skip`` asks it. ``_mark_completed``
does NOT: it recomputes ``compute_next_run(job.schedule)`` unconditionally and writes
``status='pending'`` back. Two copies of one rule, and only one of them asks.

WHY THE SCHEDULE STRING CANNOT BE THE FIX. "manual" is not a cadence, so
``compute_next_run`` falls through daily@ / at / every / in / croniter and its
except branch returns "+1 day". Tightening that would only change WHICH wrong
cadence a one-shot gets. The marker is the answer, and it already exists.

THE TERMINAL STATE ALREADY EXISTS TOO. ``jobs.status`` is
``CHECK (status IN ('pending','running','completed','failed'))`` and 'completed'
is used by NOTHING — 126 pending, 1 failed, 0 completed. A method named
``_mark_completed`` has never written it.

NOT DELETED, DELIBERATELY. ``job_runs.job_id`` is a real FK to ``jobs`` and the
run history is the record of what the platform did; a completed row stays
inspectable and stays out of the claim query, which selects
``status='pending' AND enabled=1``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.scheduler.scheduler_helpers import insert_job

pytestmark = pytest.mark.asyncio


class _Succeeds(JobHandler):
    def __init__(self, name: str) -> None:
        self._name = name
        self.calls = 0

    @property
    def handler_name(self) -> str:
        return self._name

    @property
    def trigger_kind(self) -> str:  # type: ignore[override]
        return "on_demand"

    async def execute(self, job: Job) -> JobResult:
        self.calls += 1
        return JobResult(
            job_id=job.job_id, success=True, output="ok", error=None, duration_ms=1.0
        )


class _SelfDeletes(_Succeeds):
    """goal_execution's shape: the handler removes its own row before returning."""

    def __init__(self, name: str, db: DbPool) -> None:
        super().__init__(name)
        self._db = db

    async def execute(self, job: Job) -> JobResult:
        await self._db.execute("DELETE FROM jobs WHERE job_id = ?", (job.job_id,))
        return await super().execute(job)


def _job(handler: str, **overrides: Any) -> Job:
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    defaults: dict[str, Any] = dict(
        job_id=f"{handler}-{uuid.uuid4().hex[:6]}",
        handler_name=handler,
        # The LIVE value. A tidy "daily@08:00" fixture would not exercise the
        # fall-through that turns a one-shot into a daily job.
        schedule="manual",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=past,
        status="pending",
        params={"run_once": True},
    )
    defaults.update(overrides)
    return Job(**defaults)


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    HandlerRegistry.reset()
    yield
    HandlerRegistry.reset()


@pytest.fixture(autouse=True)
def _allow_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *_a, **_kw: None,
    )


def _sched(db: DbPool, handler: JobHandler) -> JobScheduler:
    reg = HandlerRegistry.instance()
    reg.register(handler)
    return JobScheduler(db=db, handler_registry=reg)


async def _row(db: DbPool, job_id: str) -> dict[str, Any] | None:
    rows = await db.fetch_all("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    return dict(rows[0]) if rows else None


async def test_a_run_once_job_goes_TERMINAL_instead_of_coming_back(tmp_db: DbPool) -> None:
    """The live rollover_summary case, 92 rows of it."""
    handler = _Succeeds("rollover_summary")
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)

    await sched._poll()

    row = await _row(tmp_db, job.job_id)
    assert row is not None, "a completed one-shot stays inspectable; it is not deleted"
    assert row["status"] == "completed", (
        "a one-shot re-armed to 'pending' fires again tomorrow — 92 rollover_summary "
        "rows on the live box are queued to do exactly that"
    )


async def test_it_does_not_run_a_SECOND_time(tmp_db: DbPool) -> None:
    """The behaviour that matters, measured end to end rather than by column."""
    handler = _Succeeds("rollover_summary")
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)

    await sched._poll()
    await tmp_db.execute(
        "UPDATE jobs SET next_run_at = ? WHERE job_id = ?",
        ((datetime.now(UTC) - timedelta(days=1)).isoformat(), job.job_id),
    )
    await sched._poll()

    assert handler.calls == 1, "the one-shot ran twice"


async def test_the_next_run_slot_is_NOT_pushed_a_day_out(tmp_db: DbPool) -> None:
    """"manual" is not a cadence. Recomputing one is how +1d got written 326 times."""
    handler = _Succeeds("rollover_summary")
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)

    await sched._poll()

    row = await _row(tmp_db, job.job_id)
    assert row is not None
    assert row["next_run_at"] == job.next_run_at, (
        "the slot was recomputed for a job that has no next occurrence"
    )


async def test_a_RECURRING_job_is_completely_unchanged(tmp_db: DbPool) -> None:
    """The expensive direction. Every seeded standing job has no run_once marker,
    and all of them must keep re-arming."""
    handler = _Succeeds("health_sweep")
    sched = _sched(tmp_db, handler)
    job = _job("health_sweep", schedule="every 5m", params={})
    await insert_job(tmp_db, job)

    await sched._poll()

    row = await _row(tmp_db, job.job_id)
    assert row is not None
    assert row["status"] == "pending"
    assert row["next_run_at"] != job.next_run_at, "a recurring job must advance"


async def test_the_run_history_is_still_written_for_a_one_shot(tmp_db: DbPool) -> None:
    """job_runs is the record of what the platform actually did. Going terminal
    must not cost it — which is why the row is completed, never deleted."""
    handler = _Succeeds("rollover_summary")
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)

    await sched._poll()

    rows = await tmp_db.fetch_all(
        "SELECT status FROM job_runs WHERE job_id = ?", (job.job_id,)
    )
    assert [dict(r)["status"] for r in rows] == ["completed"]


async def test_the_SELF_DELETING_handler_path_still_works(tmp_db: DbPool) -> None:
    """goal_execution removes its own row before returning; the rowcount==0 branch
    that exists for it must survive, or every successful reminder raises on the
    job_runs foreign key."""
    handler = _SelfDeletes("goal_execution", tmp_db)
    sched = _sched(tmp_db, handler)
    job = _job("goal_execution")
    await insert_job(tmp_db, job)

    await sched._poll()

    assert await _row(tmp_db, job.job_id) is None
    rows = await tmp_db.fetch_all(
        "SELECT run_id FROM job_runs WHERE job_id = ?", (job.job_id,)
    )
    assert rows == [], "no history row may be written for a job that no longer exists"


async def test_the_terminal_state_is_one_the_SCHEMA_allows() -> None:
    """'completed' is in the CHECK constraint and was used by nothing — 126
    pending, 1 failed, 0 completed on the live table. A method called
    _mark_completed had never written it."""
    from stackowl.scheduler.scheduler import ONE_SHOT_TERMINAL_STATUS

    assert ONE_SHOT_TERMINAL_STATUS == "completed"


async def test_the_completed_row_cannot_be_CLAIMED_again(tmp_db: DbPool) -> None:
    """The claim query is `status='pending' AND enabled=1`, so a terminal row is
    structurally out of the loop rather than merely dated far ahead."""
    handler = _Succeeds("rollover_summary")
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)
    await sched._poll()

    rows = await tmp_db.fetch_all(
        "SELECT job_id FROM jobs WHERE status = 'pending' AND enabled = 1"
    )
    assert [dict(r)["job_id"] for r in rows] == []
