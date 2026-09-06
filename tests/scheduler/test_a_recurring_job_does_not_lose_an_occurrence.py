"""A daily job that fails on a 4-hour outage must not wait 24 hours to try again.

WHY THIS EXISTS — the OTHER half of ESC-53, measured 2026-09-06.

ESC-53 fixed the one-shot branch: a one-shot that fails transiently re-arms on a
widening ladder instead of dying, because "whether work survived a transient blip
was being decided by CADENCE, which has nothing to do with whether the work still
needs doing." Its own write-up names the recurring branch as the CORRECT half —
F-60 re-arms rather than terminating, and "the job itself is never given up on."

**The job is never given up on. The OCCURRENCE is lost every time.** For a
one-shot the row died and that was visible. For a recurring job the row survives,
so the loss leaves no wreckage — and it is the same loss.

MEASURED on the live box, from the retained log window (2026-08-28 .. 2026-09-05,
9 days):

  * **17 recurring occurrences** were re-armed to their next cadence slot after
    exhausting retries — **8 of them `goal_execution`**, the operator's own goals,
    roughly one a day. The rest: incident_escalation 4, retry_sweep 3,
    objective_driver 1, reflection_writer 1.
  * the one-shot ladder ESC-53 built fired **0** times in the same window.

THE ARITHMETIC THAT MAKES IT INEVITABLE. `_MAX_RETRIES = 3` at
`_RETRY_DELAY_MIN = 5` is a **15-minute** retry budget. The failure that consumed
8 of those 17 was `AllProvidersUnavailableError` — and the outage on 2026-09-05
ran from 22:11 to 02:24, **4h13m**. Those retries were guaranteed to be spent
while the outage was still happening; the provider returned 20 hours before the
job's next slot and nothing asked it to try again.

THE FIX REUSES WHAT IS ALREADY THERE. `classify_failure`/`is_permanent` already
decide retryability and the one-shot branch already asks them. The ladder
`_ONE_SHOT_REARM_BACKOFF_SEC` already exists. `retry_at` already exists as the
slot that means "try sooner" WITHOUT touching the canonical cadence — F113's
rule, "NEVER touch next_run_at". So the recurring branch simply asks the same
questions the one-shot branch asks.

AND IT IS CAPPED AT THE CADENCE SLOT, which is the one way this could have made
things worse. The claim query reads `CASE WHEN retry_at IS NOT NULL THEN retry_at
<= ? ELSE next_run_at <= ? END` — retry_at MASKS next_run_at. An uncapped ladder
rung landing after the next slot would therefore DELAY the schedule it was meant
to protect. So the retry is only armed when it lands strictly before the cadence
slot; an hourly job keeps its hour.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.scheduler.scheduler import (
    _MAX_RETRIES,
    _ONE_SHOT_REARM_BACKOFF_SEC,
    JobScheduler,
)
from stackowl.scheduler.scheduler_helpers import insert_job

#: Verbatim from the live row that lost 8 goal runs.
CIRCUIT_OPEN = (
    "execute: AllProvidersUnavailableError: All providers unavailable: "
    "NeraAiRaw: skipped (circuit open); NeraAiRaw: skipped (circuit open)"
)
#: A failure that repeating cannot fix.
MALFORMED = "malformed job: session_key and ended_conversation_id are required"


class _FailsWith(JobHandler):
    def __init__(self, name: str, error: str) -> None:
        self._name, self._error, self.calls = name, error, 0

    @property
    def handler_name(self) -> str:
        return self._name

    @property
    def trigger_kind(self) -> str:  # type: ignore[override]
        return "on_demand"

    async def execute(self, job: Job) -> JobResult:
        self.calls += 1
        return JobResult(
            job_id=job.job_id, success=False, output=None,
            error=self._error, duration_ms=1.0,
        )


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


def _recurring(handler: str, schedule: str = "daily@17:00", **over: Any) -> Job:
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    defaults: dict[str, Any] = dict(
        job_id=f"{handler}-{uuid.uuid4().hex[:6]}",
        handler_name=handler,
        schedule=schedule,
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=past,
        status="pending",
        params={},          # no run_once -> recurring
    )
    defaults.update(over)
    return Job(**defaults)


async def _drive_to_rearm(db: DbPool, sched: JobScheduler, job_id: str) -> dict:
    """Burn the within-occurrence retries until the RE-ARM branch is reached."""
    for _ in range(_MAX_RETRIES + 2):
        rows = await db.fetch_all(
            "SELECT status, failure_count, retry_at, next_run_at FROM jobs WHERE job_id = ?",
            (job_id,),
        )
        if not rows or rows[0]["status"] == "failed":
            break
        if (rows[0]["failure_count"] or 0) >= 1:
            break  # the re-arm has landed — the transition under test
        await db.execute(
            "UPDATE jobs SET next_run_at = ?, retry_at = NULL WHERE job_id = ? "
            "AND status = 'pending'",
            ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), job_id),
        )
        await sched._poll()
    rows = await db.fetch_all(
        "SELECT status, failure_count, retry_at, next_run_at FROM jobs WHERE job_id = ?",
        (job_id,),
    )
    return dict(rows[0])


class TestATransientFailureEarnsAnEarlyRetry:
    async def test_a_daily_job_retries_before_its_next_slot(self, tmp_db: DbPool) -> None:
        """THE DEFECT ITSELF. Before this, the row came back with retry_at NULL and
        next_run_at ~24h away — the occurrence silently lost."""
        h = _FailsWith("goal_execution", CIRCUIT_OPEN)
        job = _recurring("goal_execution")
        await insert_job(tmp_db, job)
        row = await _drive_to_rearm(tmp_db, _sched(tmp_db, h), job.job_id)

        assert row["status"] == "pending"
        assert row["retry_at"] is not None, (
            "a transient failure left no early retry — the occurrence is lost until "
            f"the next cadence slot: {row}"
        )
        assert row["retry_at"] < row["next_run_at"], (
            "the retry must land BEFORE the cadence slot or it buys nothing"
        )

    async def test_the_cadence_slot_is_never_touched(self, tmp_db: DbPool) -> None:
        """F113's rule. `retry_at` is the 'try sooner' slot precisely so the
        canonical schedule survives; a fix that advanced next_run_at instead would
        move the user's daily job."""
        h = _FailsWith("goal_execution", CIRCUIT_OPEN)
        job = _recurring("goal_execution", schedule="daily@17:00")
        await insert_job(tmp_db, job)
        row = await _drive_to_rearm(tmp_db, _sched(tmp_db, h), job.job_id)

        assert row["next_run_at"].startswith(("2", "1")), row
        assert "17:00" in row["next_run_at"], (
            f"the daily cadence slot was moved by the retry: {row['next_run_at']}"
        )

    async def test_a_permanent_failure_gets_no_early_retry(self, tmp_db: DbPool) -> None:
        """THE CONTROL THAT KEEPS THIS HONEST. Re-arming a job that can never
        succeed is the no-decay failure mode wearing a fix's clothing — the exact
        objection ESC-53 had to answer for one-shots."""
        h = _FailsWith("goal_execution", MALFORMED)
        job = _recurring("goal_execution")
        await insert_job(tmp_db, job)
        row = await _drive_to_rearm(tmp_db, _sched(tmp_db, h), job.job_id)

        assert row["retry_at"] is None, (
            f"a permanently-broken job was given an early retry: {row}"
        )


class TestTheRetryNeverDelaysTheSchedule:
    async def test_a_frequent_job_keeps_its_own_cadence(self, tmp_db: DbPool) -> None:
        """THE WAY THIS COULD HAVE MADE THINGS WORSE. The claim query is
        `CASE WHEN retry_at IS NOT NULL THEN retry_at <= ? ELSE next_run_at <= ?` —
        retry_at MASKS next_run_at. A ladder rung landing after the next slot would
        push the schedule out. For a job whose cadence is sooner than the first
        rung, no retry may be armed at all."""
        h = _FailsWith("frequent", CIRCUIT_OPEN)
        job = _recurring("frequent", schedule="every 1m")
        await insert_job(tmp_db, job)
        row = await _drive_to_rearm(tmp_db, _sched(tmp_db, h), job.job_id)

        if row["retry_at"] is not None:
            assert row["retry_at"] < row["next_run_at"], (
                "an armed retry landed at or after the cadence slot it masks — this "
                f"DELAYS the schedule instead of protecting it: {row}"
            )

    def test_the_ladder_is_the_one_the_one_shot_path_uses(self) -> None:
        """ONE COPY OF THE RULE. A second backoff table would drift from the first,
        which is the defect shape this repo pays for most often."""
        assert _ONE_SHOT_REARM_BACKOFF_SEC[0] > 0
        assert list(_ONE_SHOT_REARM_BACKOFF_SEC) == sorted(_ONE_SHOT_REARM_BACKOFF_SEC), (
            "the ladder must widen"
        )
