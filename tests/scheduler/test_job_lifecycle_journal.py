"""Every scheduler job-lifecycle transition records its own journal event, in
the same transaction as the change (Spec 2.6 AC). Mirrors
``tests/pipeline/durable/test_journal_wiring.py``'s style for Story 2.1's task
events, applied to ``job.started``/``job.finished``/``job.failed``/``job.parked``.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.scheduler.scheduler import _MAX_RETRIES, JobScheduler
from stackowl.scheduler.scheduler_helpers import insert_job

pytestmark = pytest.mark.asyncio


class _ScriptedHandler(JobHandler):
    """A handler whose outcomes are scripted call-by-call; repeats its last
    scripted result once the script runs out (mirrors `_AlwaysFailsHandler`'s
    always-fail shape, generalised to a short success/fail sequence)."""

    def __init__(self, name: str, outcomes: list[bool], error: str = "boom") -> None:
        self._name = name
        self._outcomes = outcomes
        self._error = error
        self.calls = 0

    @property
    def handler_name(self) -> str:
        return self._name

    async def execute(self, job: Job) -> JobResult:
        idx = min(self.calls, len(self._outcomes) - 1)
        success = self._outcomes[idx]
        self.calls += 1
        return JobResult(
            job_id=job.job_id, success=success, output="ok" if success else None,
            error=None if success else self._error, duration_ms=1.0,
        )


def _job(handler: str, *, params: dict[str, Any] | None = None, **overrides: Any) -> Job:
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    defaults: dict[str, Any] = dict(
        job_id=f"{handler}-{uuid.uuid4().hex[:6]}",
        handler_name=handler,
        schedule="daily@08:00",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=past,
        status="pending",
        params=params or {},
    )
    defaults.update(overrides)
    return Job(**defaults)


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    HandlerRegistry.reset()
    yield
    HandlerRegistry.reset()


@pytest.fixture(autouse=True)
def _no_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *_a, **_kw: None,
    )


def _sched(db: DbPool, handler: JobHandler) -> JobScheduler:
    reg = HandlerRegistry.instance()
    reg.register(handler)
    return JobScheduler(db=db, handler_registry=reg)


async def _journal_rows(db: DbPool, event_type: str, job_id: str) -> list[dict]:
    return await db.fetch_all(
        "SELECT * FROM journal_events WHERE type = ? AND target_id = ?",
        (event_type, job_id),
    )


class TestStartedRecordsOnlyOnAWonClaim:
    async def test_a_dispatched_job_writes_exactly_one_job_started_row(
        self, tmp_db: DbPool,
    ) -> None:
        handler = _ScriptedHandler("morning_brief", [True])
        sched = _sched(tmp_db, handler)
        job = _job("morning_brief")
        await insert_job(tmp_db, job)

        await sched._poll()

        rows = await _journal_rows(tmp_db, "job.started", job.job_id)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "ok"
        ref = json.loads(rows[0]["record_ref"])
        assert ref["kind"] == "sqlite"
        assert ref["locator"]["table"] == "jobs"
        assert ref["locator"]["job_id"] == job.job_id
        assert json.loads(rows[0]["attrs"])["handler_name"] == "morning_brief"

    async def test_a_row_already_running_writes_no_started_event(
        self, tmp_db: DbPool,
    ) -> None:
        handler = _ScriptedHandler("check_in", [True])
        sched = _sched(tmp_db, handler)
        job = _job("check_in", status="running")
        await insert_job(tmp_db, job)

        await sched._poll()  # poll selects only status='pending' — never dispatches

        rows = await _journal_rows(tmp_db, "job.started", job.job_id)
        assert rows == []


class TestFinishedRecordsBothOneShotAndRecurring:
    async def test_a_recurring_job_completion_writes_job_finished(
        self, tmp_db: DbPool,
    ) -> None:
        handler = _ScriptedHandler("knowledge_prune", [True])
        sched = _sched(tmp_db, handler)
        job = _job("knowledge_prune")
        await insert_job(tmp_db, job)

        await sched._poll()

        rows = await _journal_rows(tmp_db, "job.finished", job.job_id)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "ok"
        # The row survives — a recurring job re-arms rather than deleting.
        remaining = await tmp_db.fetch_all(
            "SELECT status FROM jobs WHERE job_id = ?", (job.job_id,)
        )
        assert remaining[0]["status"] == "pending"

    async def test_a_one_shot_completion_writes_job_finished_then_retires_the_row(
        self, tmp_db: DbPool,
    ) -> None:
        handler = _ScriptedHandler("goal_execution", [True])
        sched = _sched(tmp_db, handler)
        job = _job("goal_execution", params={"run_once": True})
        await insert_job(tmp_db, job)

        await sched._poll()

        rows = await _journal_rows(tmp_db, "job.finished", job.job_id)
        assert len(rows) == 1
        # AD-4's disclosed "target already gone -> expired" case — the row IS
        # deleted by the same call path, same as the task-store precedent.
        remaining = await tmp_db.fetch_all(
            "SELECT 1 FROM jobs WHERE job_id = ?", (job.job_id,)
        )
        assert remaining == []


class TestFailedNeverParksARecurringJob:
    async def test_a_mid_run_retry_writes_job_failed(self, tmp_db: DbPool) -> None:
        """`_settle`'s retry branch — still within the retry budget."""
        handler = _ScriptedHandler("check_in", [False])
        sched = _sched(tmp_db, handler)
        job = _job("check_in")
        await insert_job(tmp_db, job)

        await sched._poll()

        rows = await _journal_rows(tmp_db, "job.failed", job.job_id)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "failed"
        assert await _journal_rows(tmp_db, "job.parked", job.job_id) == []

    async def test_a_recurring_job_exhausting_retries_re_arms_with_job_failed_never_parked(
        self, tmp_db: DbPool,
    ) -> None:
        handler = _ScriptedHandler("morning_brief", [False] * (_MAX_RETRIES + 2))
        sched = _sched(tmp_db, handler)
        job = _job("morning_brief")
        await insert_job(tmp_db, job)

        now = datetime.now(UTC)
        for _ in range(_MAX_RETRIES + 2):
            past = (now - timedelta(minutes=1)).isoformat()
            await tmp_db.execute(
                "UPDATE jobs SET next_run_at = ?, retry_at = NULL WHERE job_id = ? "
                "AND status = 'pending'",
                (past, job.job_id),
            )
            await sched._poll()
            rows = await tmp_db.fetch_all(
                "SELECT status, next_run_at FROM jobs WHERE job_id = ?", (job.job_id,)
            )
            if not rows or datetime.fromisoformat(rows[0]["next_run_at"]) > now:
                break

        assert await _journal_rows(tmp_db, "job.parked", job.job_id) == [], (
            "F-60: a recurring job never parks — no circuit breaker"
        )
        failed_rows = await _journal_rows(tmp_db, "job.failed", job.job_id)
        assert len(failed_rows) >= 1


class TestParkedIsTheOneShotGiveUp:
    async def test_a_one_shot_permanent_failure_writes_job_parked(
        self, tmp_db: DbPool,
    ) -> None:
        # A single run never reaches `_mark_failed`'s permanence check: `_settle`'s
        # retry branch always tries a retry first for a scheduled job (ADR-2's
        # transient-by-policy authority), regardless of the error's shape.
        # Permanence is judged only once the retry BUDGET is exhausted — so this
        # drives the same number of cycles `_exhaust_retries` does in
        # `test_f60_rearm_recurring_on_failure.py`.
        handler = _ScriptedHandler(
            "goal_execution", [False] * (_MAX_RETRIES + 2),
            error="malformed job: session_key and ended_conversation_id are required",
        )
        sched = _sched(tmp_db, handler)
        job = _job("goal_execution", params={"run_once": True})
        await insert_job(tmp_db, job)

        now = datetime.now(UTC)
        for _ in range(_MAX_RETRIES + 2):
            past = (now - timedelta(minutes=1)).isoformat()
            await tmp_db.execute(
                "UPDATE jobs SET next_run_at = ?, retry_at = NULL WHERE job_id = ? "
                "AND status = 'pending'",
                (past, job.job_id),
            )
            await sched._poll()
            rows = await tmp_db.fetch_all(
                "SELECT status FROM jobs WHERE job_id = ?", (job.job_id,)
            )
            if not rows:
                break

        rows = await _journal_rows(tmp_db, "job.parked", job.job_id)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "parked"
        assert rows[0]["attention"] == "needs_you"
        assert rows[0]["intensity"] == "high"
        # The row is gone — a permanently-failed one-shot is deleted (J1).
        remaining = await tmp_db.fetch_all(
            "SELECT 1 FROM jobs WHERE job_id = ?", (job.job_id,)
        )
        assert remaining == []


class TestAForcedRollbackLeavesNoEvent:
    async def test_a_failing_journal_record_rolls_back_the_claim_too(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Not just the standalone proof in ``tests/journal/test_recorder.py``
        -- forces ``journal.record`` to fail INSIDE the real wired CAS claim
        and proves the job's own UPDATE never survives either, because both
        run in the same ``DbPool.transaction()`` block (Story 2.1 precedent).
        """
        import stackowl.scheduler.scheduler as scheduler_module

        handler = _ScriptedHandler("check_in", [True])
        sched = _sched(tmp_db, handler)
        job = _job("check_in")
        await insert_job(tmp_db, job)

        async def _boom(conn: object, event: object) -> str:
            raise RuntimeError("simulated journal.record failure")

        monkeypatch.setattr(scheduler_module, "journal_record", _boom)

        with pytest.raises(RuntimeError, match="simulated journal.record failure"):
            await sched._run_job(job)

        rows = await tmp_db.fetch_all(
            "SELECT status, claimed_at FROM jobs WHERE job_id = ?", (job.job_id,)
        )
        assert rows[0]["status"] == "pending"
        assert rows[0]["claimed_at"] is None

        assert await _journal_rows(tmp_db, "job.started", job.job_id) == []
