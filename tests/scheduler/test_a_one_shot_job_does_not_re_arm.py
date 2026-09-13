"""A finished one-shot job is DELETED — never re-armed, and never parked.

THIS FILE HAS ASSERTED THE WRONG ENDING ONCE, so the history is the point.

2026-08-31. 92 ``rollover_summary`` rows sat on the live table ``status='pending'``
with ``next_run_at`` TOMORROW, armed to re-summarise conversations that had ended
once — 326 log lines of ``compute_next_run: cron parse failed — defaulting to +1d
{'schedule': 'manual'}``. ``_mark_completed`` recomputed a cadence for a job that has
none. The fix asked the ``run_once`` marker, which was right, and PARKED the row as
``status='completed'`` "to keep its job_runs history", which was not.

2026-09-12, measured on the owner's box: 136 of 169 ``enabled=1`` jobs were those
parked rows, and every reader of the schedule — scheduler health,
``/api/v1/schedules``, the Bridge — reported finished work as live schedules. The
reason for keeping them was already stale when it was written: migration 0080 made
``job_runs.job_id`` ``ON DELETE CASCADE``, and ``goal_execution`` one-shots already
deleted themselves. Two rules for one fact, and the one ``rollover_summary`` followed
never retired anything. More copies lived in ``run_now``, ``recover()``, ``resume()``
and the startup reaper, each of which pushed a one-shot's slot a day out.

ONE RULE NOW, IN ONE HOME. A ``run_once`` job that finishes — succeeded, or failed
with its retries exhausted — is deleted by the scheduler where its outcome is
recorded; ``run_now`` settles a one-shot through the same disposition. A terminal
failure is written to the audit log FIRST; when that record or the delete cannot be
written the row stays as the record, and the poll cycle retires it later. Migration
0143 removes the rows the retired rule left behind.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from stackowl.audit import logger as audit_logger
from stackowl.db.pool import DbPool
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.scheduler.scheduler import _MAX_RETRIES, JobScheduler
from stackowl.scheduler.scheduler_helpers import insert_job, reap_stale_running

pytestmark = pytest.mark.asyncio

#: Verbatim from the nine rollover jobs that could never have succeeded — the
#: shared classifier calls it permanent, so it reaches the TERMINAL branch.
MALFORMED = "malformed job: session_key and ended_conversation_id are required"
#: Verbatim from the 54 lost rollover summaries — transient, so it retries.
CIRCUIT_OPEN = (
    "summary call failed: All providers unavailable: NeraAiRaw: skipped "
    "(circuit open); NeraAiRaw: skipped (circuit open)"
)


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


class _Fails(_Succeeds):
    def __init__(self, name: str, error: str) -> None:
        super().__init__(name)
        self._error = error

    async def execute(self, job: Job) -> JobResult:
        self.calls += 1
        return JobResult(
            job_id=job.job_id, success=False, output=None, error=self._error,
            duration_ms=1.0,
        )


class _Vetoed(_Succeeds):
    """Claims success, and was checked and found NOT to have done it."""

    async def execute(self, job: Job) -> JobResult:
        self.calls += 1
        return JobResult(
            job_id=job.job_id, success=True, verified=False, output="ok",
            error="post-condition not observed", duration_ms=1.0,
        )


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


def _sched(db: DbPool, handler: JobHandler | None = None) -> JobScheduler:
    reg = HandlerRegistry.instance()
    if handler is not None:
        reg.register(handler)
    return JobScheduler(db=db, handler_registry=reg)


async def _row(db: DbPool, job_id: str) -> dict[str, Any] | None:
    rows = await db.fetch_all("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    return dict(rows[0]) if rows else None


async def _terminal_audits(db: DbPool, job_id: str) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT details FROM audit_log WHERE event_type = 'job_failed_terminal' "
        "AND target = ?",
        (job_id,),
    )


async def _record_completed_run(db: DbPool, job: Job) -> None:
    """A one-shot that ran and whose retirement failed: its run is on record."""
    await db.execute(
        "INSERT INTO job_runs (run_id, job_id, idempotency_key, status, duration_ms, "
        "ran_at) VALUES (?, ?, ?, 'completed', 1.0, ?)",
        (uuid.uuid4().hex, job.job_id, f"{job.idempotency_key}@{job.next_run_at}",
         datetime.now(UTC).isoformat()),
    )


async def _drive_to_exhaustion(db: DbPool, sched: JobScheduler, job_id: str) -> None:
    """Burn the within-occurrence retries until ``_mark_failed`` has decided."""
    for _ in range(_MAX_RETRIES + 2):
        past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        await db.execute(
            "UPDATE jobs SET next_run_at = ?, retry_at = NULL "
            "WHERE job_id = ? AND status = 'pending'",
            (past, job_id),
        )
        await sched._poll()
        row = await _row(db, job_id)
        if row is None or row["status"] == "failed" or (row["failure_count"] or 0) >= 1:
            return


def _fail_sql_once(monkeypatch: pytest.MonkeyPatch, method: str, prefix: str) -> list[str]:
    """Make ``DbPool.<method>`` raise 'database is locked' the FIRST time it runs
    a statement starting with ``prefix``. Returns the list the failure is noted in."""
    real = getattr(DbPool, method)
    failed: list[str] = []

    async def _flaky(self: DbPool, sql: str, params: Any = ()) -> Any:
        if sql.lstrip().upper().startswith(prefix) and not failed:
            failed.append(sql)
            raise sqlite3.OperationalError("database is locked")
        return await real(self, sql, params)

    monkeypatch.setattr(DbPool, method, _flaky)
    return failed


# ------------------------------------------------------------------ success


async def test_a_finished_one_shot_is_DELETED(tmp_db: DbPool) -> None:
    """The live rollover_summary case — 136 rows of it on 2026-09-12."""
    handler = _Succeeds("rollover_summary")
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)

    await sched._poll()

    assert handler.calls == 1
    assert await _row(tmp_db, job.job_id) is None, (
        "a finished one-shot left in `jobs` is counted as a live schedule by every "
        "reader — 136 of 169 enabled jobs on the owner's box were exactly this"
    )


async def test_it_does_not_run_a_SECOND_time(tmp_db: DbPool) -> None:
    """The behaviour that matters, measured end to end rather than by column."""
    handler = _Succeeds("rollover_summary")
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)

    await sched._poll()
    await sched._poll()

    assert handler.calls == 1, "the one-shot ran twice"


async def test_no_run_history_outlives_its_job(tmp_db: DbPool) -> None:
    """``job_runs.job_id`` cascades (migration 0080). History that points at a job
    that no longer exists is not a record, it is an orphan."""
    handler = _Succeeds("rollover_summary")
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)

    await sched._poll()

    rows = await tmp_db.fetch_all(
        "SELECT run_id FROM job_runs WHERE job_id = ?", (job.job_id,)
    )
    assert rows == []


async def test_a_RECURRING_job_is_completely_unchanged(tmp_db: DbPool) -> None:
    """The expensive direction. Every seeded standing job has no run_once marker,
    and all of them must keep re-arming — with their history."""
    handler = _Succeeds("health_sweep")
    sched = _sched(tmp_db, handler)
    job = _job("health_sweep", schedule="every 5m", params={})
    await insert_job(tmp_db, job)

    await sched._poll()

    row = await _row(tmp_db, job.job_id)
    assert row is not None
    assert row["status"] == "pending"
    assert row["next_run_at"] != job.next_run_at, "a recurring job must advance"
    runs = await tmp_db.fetch_all(
        "SELECT status FROM job_runs WHERE job_id = ?", (job.job_id,)
    )
    assert [r["status"] for r in runs] == ["completed"]


async def test_a_delete_that_FAILS_does_not_run_the_job_again(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row survives a failed delete, so something must stop it running twice.

    Its occurrence is recorded completed, which the dispatch dedup reads — across a
    RESTART too, where the startup reaper used to push a one-shot's slot a day out
    and change the very key that dedup reads. The next poll that reaches the row
    retires it: the platform heals its own failed delete.
    """
    failed = _fail_sql_once(monkeypatch, "execute_returning_rowcount", "DELETE FROM JOBS")
    handler = _Succeeds("rollover_summary")
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)

    await sched._poll()
    assert failed, "the delete was never attempted — the test proves nothing"
    assert await _row(tmp_db, job.job_id) is not None

    await reap_stale_running(tmp_db)  # what the next boot does to a 'running' row
    await sched._poll()

    assert handler.calls == 1, "a one-shot whose delete failed ran a second time"
    assert await _row(tmp_db, job.job_id) is None, "and the row was never retired"


async def test_a_success_whose_delete_AND_run_record_both_fail_SAYS_so(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The last guard failing too is the one case that may cost a second run, so it
    must be an ERROR that names that cost — never a quiet exit."""
    _fail_sql_once(monkeypatch, "execute_returning_rowcount", "DELETE FROM JOBS")
    _fail_sql_once(monkeypatch, "execute", "INSERT INTO JOB_RUNS")
    handler = _Succeeds("rollover_summary")
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)

    with caplog.at_level(logging.ERROR):
        await sched._poll()

    assert await _row(tmp_db, job.job_id) is not None
    runs = await tmp_db.fetch_all("SELECT run_id FROM job_runs WHERE job_id = ?", (job.job_id,))
    assert runs == []
    assert any("NOR its run recorded" in r.getMessage() for r in caplog.records), (
        "a one-shot that may run again did so without an ERROR saying so"
    )


# ------------------------------------------------------------------ failure


async def test_a_one_shot_with_retries_LEFT_is_kept_for_the_retry(tmp_db: DbPool) -> None:
    handler = _Fails("rollover_summary", CIRCUIT_OPEN)
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)

    await sched._poll()

    row = await _row(tmp_db, job.job_id)
    assert row is not None, "a one-shot with retries left was deleted"
    assert row["status"] == "pending"
    assert row["retry_count"] == 1
    assert row["retry_at"] is not None


async def test_a_TERMINAL_failure_is_recorded_then_deleted(tmp_db: DbPool) -> None:
    """Owner decision J1 (2026-09-12): failed one-shots are deleted too — after the
    failure is recorded where failures belong."""
    handler = _Fails("rollover_summary", MALFORMED)
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)

    await _drive_to_exhaustion(tmp_db, sched, job.job_id)

    assert await _row(tmp_db, job.job_id) is None
    audit = await _terminal_audits(tmp_db, job.job_id)
    assert len(audit) == 1, "the row went but its failure was never recorded"
    assert "malformed job" in audit[0]["details"]


async def test_a_terminal_failure_that_cannot_be_RECORDED_keeps_its_row(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting the only record of a failure is losing it. With the audit write
    down, the row itself stays as the record — terminal, never claimed again."""

    async def _audit_down(*_a: Any, **_kw: Any) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(audit_logger, "chain_append_via_pool", _audit_down)
    handler = _Fails("rollover_summary", MALFORMED)
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)

    await _drive_to_exhaustion(tmp_db, sched, job.job_id)

    row = await _row(tmp_db, job.job_id)
    assert row is not None, "the failure's only record was deleted"
    assert row["status"] == "failed"
    assert row["last_error"] == MALFORMED


async def test_a_terminal_failure_whose_DELETE_fails_keeps_its_row_and_ONE_record(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = _fail_sql_once(monkeypatch, "execute_returning_rowcount", "DELETE FROM JOBS")
    handler = _Fails("rollover_summary", MALFORMED)
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)

    await _drive_to_exhaustion(tmp_db, sched, job.job_id)

    assert failed, "the delete was never attempted — the test proves nothing"
    row = await _row(tmp_db, job.job_id)
    assert row is not None
    assert row["status"] == "failed", "left 'running', a reaper would run it again"
    assert row["last_error"] == MALFORMED
    assert len(await _terminal_audits(tmp_db, job.job_id)) == 1


# ------------------------------------------------------------------ self-heal


async def test_the_poll_cycle_HEALS_a_terminal_one_shot_whose_delete_failed(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migration 0143 runs once. Without a retry, a row whose delete failed would
    count as a live schedule for good."""
    _fail_sql_once(monkeypatch, "execute_returning_rowcount", "DELETE FROM JOBS")
    handler = _Fails("rollover_summary", MALFORMED)
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)
    await _drive_to_exhaustion(tmp_db, sched, job.job_id)
    assert await _row(tmp_db, job.job_id) is not None

    await sched._poll_cycle()

    assert await _row(tmp_db, job.job_id) is None, "nothing retried the retirement"
    assert len(await _terminal_audits(tmp_db, job.job_id)) == 1, "recorded twice"


async def test_the_poll_cycle_RECORDS_then_retires_a_failure_never_recorded(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = audit_logger.chain_append_via_pool

    async def _audit_down(*_a: Any, **_kw: Any) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(audit_logger, "chain_append_via_pool", _audit_down)
    handler = _Fails("rollover_summary", MALFORMED)
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)
    await _drive_to_exhaustion(tmp_db, sched, job.job_id)
    assert await _row(tmp_db, job.job_id) is not None

    monkeypatch.setattr(audit_logger, "chain_append_via_pool", real)  # the disk recovers
    await sched._poll_cycle()

    assert await _row(tmp_db, job.job_id) is None
    audit = await _terminal_audits(tmp_db, job.job_id)
    assert len(audit) == 1, "the row went before its failure was recorded"
    assert "malformed job" in audit[0]["details"]


async def test_the_heal_leaves_a_PAUSED_one_shot_alone(tmp_db: DbPool) -> None:
    """pause() writes status='failed' with enabled=0 — the user's decision, not a
    finished job."""
    sched = _sched(tmp_db)
    job = _job("rollover_summary", status="failed", enabled=False)
    await insert_job(tmp_db, job)

    await sched._poll_cycle()

    assert await _row(tmp_db, job.job_id) is not None


# ------------------------------------------------------------------ run_now


async def test_run_now_on_a_one_shot_DELETES_it(tmp_db: DbPool) -> None:
    """The second copy of the rule: run_now re-armed a one-shot to 'pending' with a
    +1 day slot, so an owner-triggered reminder would fire again tomorrow."""
    handler = _Succeeds("rollover_summary")
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)

    result = await sched.run_now(job.job_id)

    assert result is not None and result.success is True
    assert handler.calls == 1
    assert await _row(tmp_db, job.job_id) is None


async def test_run_now_puts_a_failed_one_shot_on_the_RETRY_LADDER(tmp_db: DbPool) -> None:
    """Not back to 'pending' at a past slot with no retry state — the poller would
    re-run it at once and the ladder would never advance. And never a day out."""
    handler = _Fails("rollover_summary", CIRCUIT_OPEN)
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)

    await sched.run_now(job.job_id)

    row = await _row(tmp_db, job.job_id)
    assert row is not None, "run_now deleted a one-shot that never succeeded"
    assert row["status"] == "pending"
    assert row["retry_count"] == 1
    assert row["retry_at"] is not None
    assert row["next_run_at"] == job.next_run_at


async def test_run_now_a_VETOED_one_shot_is_retried_not_retired(tmp_db: DbPool) -> None:
    """success=True with verified=False is not a completed run. Recorded as one, the
    next poll's dedup deleted the row: no retry, no audit, no alert."""
    handler = _Vetoed("rollover_summary")
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)

    await sched.run_now(job.job_id)

    completed = await tmp_db.fetch_all(
        "SELECT run_id FROM job_runs WHERE job_id = ? AND status = 'completed'",
        (job.job_id,),
    )
    assert completed == [], "a vetoed success was recorded as a completed run"
    await tmp_db.execute(
        "UPDATE jobs SET retry_at = ? WHERE job_id = ?",
        ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), job.job_id),
    )
    await sched._poll()

    assert handler.calls == 2, "the vetoed one-shot was retired instead of retried"


async def test_run_now_on_a_one_shot_with_NO_handler_keeps_its_slot(tmp_db: DbPool) -> None:
    sched = _sched(tmp_db)  # nothing registered for it
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)

    result = await sched.run_now(job.job_id)

    assert result is not None and result.success is False
    row = await _row(tmp_db, job.job_id)
    assert row is not None
    assert row["status"] == "pending"
    assert row["next_run_at"] == job.next_run_at, "re-armed a day out"


async def test_run_now_RETIRES_an_already_finished_one_shot_instead_of_running_it(
    tmp_db: DbPool,
) -> None:
    """A one-shot whose retirement failed, freed by a reaper back to 'pending'."""
    handler = _Succeeds("rollover_summary")
    sched = _sched(tmp_db, handler)
    job = _job("rollover_summary")
    await insert_job(tmp_db, job)
    await _record_completed_run(tmp_db, job)

    result = await sched.run_now(job.job_id)

    assert result is not None and result.success is False
    assert handler.calls == 0, "a finished one-shot ran again"
    assert await _row(tmp_db, job.job_id) is None


# ------------------------------------------------------------------ restart


async def test_the_startup_reaper_keeps_a_one_shots_slot(tmp_db: DbPool) -> None:
    job = _job("rollover_summary", status="running")
    await insert_job(tmp_db, job)

    await reap_stale_running(tmp_db)

    row = await _row(tmp_db, job.job_id)
    assert row is not None
    assert row["status"] == "pending"
    assert row["next_run_at"] == job.next_run_at, "the reaper pushed a one-shot a day out"


async def test_recover_keeps_an_overdue_one_shots_slot_so_a_finished_one_is_not_rerun(
    tmp_db: DbPool,
) -> None:
    """Outside the replay window recover() used to recompute the slot — +1 day for
    "manual" — which changed the key the dedup reads, so a finished-but-undeleted
    one-shot ran again."""
    handler = _Succeeds("rollover_summary")
    sched = _sched(tmp_db, handler)
    job = _job(
        "rollover_summary",
        next_run_at=(datetime.now(UTC) - timedelta(days=2)).isoformat(),
    )
    await insert_job(tmp_db, job)
    await _record_completed_run(tmp_db, job)

    await sched.recover()
    row = await _row(tmp_db, job.job_id)
    assert row is not None and row["next_run_at"] == job.next_run_at

    await sched._poll()

    assert handler.calls == 0, "a finished one-shot ran again after a restart"
    assert await _row(tmp_db, job.job_id) is None
