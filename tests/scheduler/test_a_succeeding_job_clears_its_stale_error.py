"""A job that succeeds must stop reporting the failure it recovered from.

MEASURED 2026-08-24T15:11Z on the live `jobs` table. THREE healthy, currently-
running jobs were carrying an error from an earlier run:

    skill_synthesizer  last_run 08:38:12  "no provider for tier fast: All providers
                                           unavailable: NeraAiRaw: (circuit open)"
    retry_sweep        last_run 15:11:16  "handler timed out after 1200s"
    dream_worker       last_run 08-11     "unscheduled by migration 0113"

None of them had failed. `skill_synthesizer`'s 08:30 run logged
`[synth] run_all: exit` then `exit — completed`; `retry_sweep` runs every 90s and
completed in 2.1ms, with ZERO timeout warnings in the whole day's log.

THE COST IS NOT COSMETIC. I read `skill_synthesizer`'s row while confirming a
different item, concluded from `last_error` that its last run had died on the
provider circuit, and started writing that down. Only the log said otherwise. A
field that reports a failure which is no longer true costs its reader the same
detour every time — the same defect as an error message that misnames its own
cause, which this programme has already paid for once in `web_fetch`.

It also makes the row self-contradictory: `_mark_completed` resets `retry_count`,
`retry_at` AND `failure_count`, so the row ends up claiming zero failures beside a
populated error string.

AND IT IS ONE COPY OF A RULE THAT IS RIGHT EVERYWHERE ELSE — failure mode 3.
Three sibling paths already clear it on recovery:

    retry_queue_store.requeue   "... status='pending', last_error = NULL ..."
    owl_lifecycle:273           "... failure_count = 0, last_error = NULL ..."
    scheduler.resume_job        "... failure_count = 0, last_error = NULL ..."

Only `_mark_completed` was missed. `jobs.last_error` is read back out through
`scheduler_helpers` into the TUI job list, so the stale string is shown.
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

#: Verbatim from the live row, 2026-08-24. A fixture that invented a tidy error
#: string would not exercise the length or shape the column actually holds.
LIVE_STALE_ERROR = (
    "no provider for tier fast: All providers unavailable: NeraAiRaw: skipped "
    "(circuit open); NeraAiRaw: skipped (circuit open); NeraAiRaw: skipped "
    "(circuit open)"
)


class _SucceedsHandler(JobHandler):
    """The job is fine now — which is precisely the case under test."""

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


class _FailsHandler(JobHandler):
    """Used only to prove the failure path still REPORTS honestly."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def handler_name(self) -> str:
        return self._name

    @property
    def trigger_kind(self) -> str:  # type: ignore[override]
        return "on_demand"

    async def execute(self, job: Job) -> JobResult:
        return JobResult(
            job_id=job.job_id, success=False, output=None,
            error="transient boom", duration_ms=1.0,
        )


def _job(handler: str, **overrides: Any) -> Job:
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    defaults: dict[str, Any] = dict(
        job_id=f"{handler}-{uuid.uuid4().hex[:6]}",
        handler_name=handler,
        schedule="daily@08:00",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=past,
        status="pending",
        params={},
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


async def test_a_successful_run_clears_the_previous_error(tmp_db: DbPool) -> None:
    """The defect, reproduced from the live row and then fixed."""
    handler = _SucceedsHandler("skill_synthesizer")
    sched = _sched(tmp_db, handler)
    job = _job("skill_synthesizer", last_error=LIVE_STALE_ERROR)
    await insert_job(tmp_db, job)

    await sched._poll()

    assert handler.calls == 1, "the job must actually have run"
    row = (await tmp_db.fetch_all(
        "SELECT status, last_error, failure_count FROM jobs WHERE job_id = ?",
        (job.job_id,),
    ))[0]
    assert row["status"] == "pending", "a recurring job returns to pending on success"
    assert not row["last_error"], (
        "a job that just SUCCEEDED must not still report the error it recovered "
        f"from — got {row['last_error']!r}"
    )


async def test_the_row_is_not_self_contradictory(tmp_db: DbPool) -> None:
    """`failure_count = 0` beside a populated `last_error` is a row that disagrees
    with itself. _mark_completed already resets the counter; the error string was
    the one field of the failure record it left behind."""
    handler = _SucceedsHandler("retry_sweep")
    sched = _sched(tmp_db, handler)
    job = _job("retry_sweep", last_error="handler timed out after 1200s",
               failure_count=3)
    await insert_job(tmp_db, job)

    await sched._poll()

    row = (await tmp_db.fetch_all(
        "SELECT failure_count, last_error FROM jobs WHERE job_id = ?", (job.job_id,)
    ))[0]
    assert row["failure_count"] == 0
    assert not row["last_error"], (
        "failure_count and last_error are two halves of ONE failure record; "
        "clearing only one leaves the row claiming zero failures with an error"
    )


async def test_a_job_that_never_failed_is_untouched(tmp_db: DbPool) -> None:
    """The change must not invent a write on the ordinary path."""
    handler = _SucceedsHandler("digest")
    sched = _sched(tmp_db, handler)
    job = _job("digest")
    await insert_job(tmp_db, job)

    await sched._poll()

    row = (await tmp_db.fetch_all(
        "SELECT status, last_error FROM jobs WHERE job_id = ?", (job.job_id,)
    ))[0]
    assert row["status"] == "pending"
    assert not row["last_error"]


async def test_a_FAILING_job_still_records_why(tmp_db: DbPool) -> None:
    """The guarantee that must survive. Clearing on success is only safe if the
    failure path still writes the error — otherwise this trades a stale truth for
    no truth at all, which is strictly worse.
    """
    handler = _FailsHandler("morning_brief")
    sched = _sched(tmp_db, handler)
    job = _job("morning_brief")
    await insert_job(tmp_db, job)

    # One poll is enough: the first failure schedules a retry, and whichever
    # branch it lands in must leave the reason recorded somewhere on the row.
    for _ in range(6):
        await tmp_db.execute(
            "UPDATE jobs SET next_run_at = ?, retry_at = NULL WHERE job_id = ? "
            "AND status = 'pending'",
            ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), job.job_id),
        )
        await sched._poll()
        row = (await tmp_db.fetch_all(
            "SELECT last_error FROM jobs WHERE job_id = ?", (job.job_id,)
        ))[0]
        if row["last_error"]:
            break

    assert row["last_error"] == "transient boom", (
        "the failure path must still say WHY — clearing on success must not become "
        "clearing always"
    )
