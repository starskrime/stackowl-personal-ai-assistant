"""S2 F-61 + F-62 — scheduler failure observability and handler-registration recovery.

F-61: ``_mark_failed`` permanently kills (one-shot) or re-arms (recurring) a job
that exhausted its retries. Before the fix the only operator-visible signal was a
single ERROR log line. The fix routes a PROACTIVE operator notification through the
same shared delivery seam (:class:`ProactiveJobDeliverer`) that morning_brief /
check_in / goal_execution use, addressed from the job's DURABLE recipients —
best-effort and honest (no deliverer / no durable target ⇒ logged, never sent,
never raises).

F-62: a job whose handler is not registered at poll time (conditionally-registered
handlers, or registration ordered after the first poll) was marked terminally
``failed`` on the first tick — unreachable forever even once the handler registers.
The fix leaves such a job ``pending`` and warns, so a later registration recovers
it; terminal ``failed`` is reserved for handler-RAISED errors past max-retries.
"""

from __future__ import annotations

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


class _AlwaysFailsHandler(JobHandler):
    """A handler that always returns success=False — a transiently-broken job."""

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
            job_id=job.job_id, success=False, output=None, error="boom", duration_ms=1.0
        )


class _OkHandler(JobHandler):
    """A handler that succeeds — used to prove a missing-then-registered job runs."""

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
            job_id=job.job_id, success=True, output="done", error=None, duration_ms=1.0
        )


class _RecordingOutcome:
    rollup = "delivered"


class _RecordingDeliverer:
    """Captures every ``deliver_for_job`` call — stands in for ProactiveJobDeliverer."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def deliver_for_job(
        self, job: Job, *, message: str, category: str, urgency: str = "normal"
    ) -> _RecordingOutcome:
        self.calls.append(
            {
                "job_id": job.job_id,
                "message": message,
                "category": category,
                "urgency": urgency,
            }
        )
        return _RecordingOutcome()


class _RaisingDeliverer:
    """A deliverer whose send blows up — the lifecycle write must survive it."""

    async def deliver_for_job(self, job: Job, **_kw: Any) -> _RecordingOutcome:
        raise RuntimeError("transport exploded")


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
def _allow_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *_a, **_kw: None,
    )


def _sched(
    db: DbPool, handler: JobHandler | None, *, deliverer: Any = None
) -> JobScheduler:
    reg = HandlerRegistry.instance()
    if handler is not None:
        reg.register(handler)
    return JobScheduler(db=db, handler_registry=reg, job_deliverer=deliverer)


async def _exhaust_retries(db: DbPool, sched: JobScheduler, job_id: str) -> None:
    now = datetime.now(UTC)
    for _ in range(_MAX_RETRIES + 2):
        past = (now - timedelta(minutes=1)).isoformat()
        await db.execute(
            "UPDATE jobs SET next_run_at = ?, retry_at = NULL WHERE job_id = ? "
            "AND status = 'pending'",
            (past, job_id),
        )
        await sched._poll()
        rows = await db.fetch_all(
            "SELECT status, next_run_at FROM jobs WHERE job_id = ?", (job_id,)
        )
        if not rows:
            return
        if rows[0]["status"] == "failed":
            return
        if datetime.fromisoformat(rows[0]["next_run_at"]) > now:
            return


# ---------------------------------------------------------------------------
# F-61 — failure routes a proactive operator notification
# ---------------------------------------------------------------------------


async def test_terminal_one_shot_failure_routes_notification(tmp_db: DbPool) -> None:
    deliverer = _RecordingDeliverer()
    sched = _sched(tmp_db, _AlwaysFailsHandler("goal_execution"), deliverer=deliverer)
    job = _job("goal_execution", params={"run_once": True})
    await insert_job(tmp_db, job)

    await _exhaust_retries(tmp_db, sched, job.job_id)

    rows = await tmp_db.fetch_all(
        "SELECT status FROM jobs WHERE job_id = ?", (job.job_id,)
    )
    assert rows[0]["status"] == "failed"
    assert deliverer.calls, "terminal failure must route an operator notification"
    assert deliverer.calls[-1]["job_id"] == job.job_id
    assert deliverer.calls[-1]["urgency"] == "high"


async def test_recurring_rearm_routes_notification(tmp_db: DbPool) -> None:
    deliverer = _RecordingDeliverer()
    sched = _sched(tmp_db, _AlwaysFailsHandler("morning_brief"), deliverer=deliverer)
    job = _job("morning_brief")  # recurring
    await insert_job(tmp_db, job)

    await _exhaust_retries(tmp_db, sched, job.job_id)

    assert deliverer.calls, "recurring re-arm (an outage) must route a notification"
    assert deliverer.calls[-1]["category"]


async def test_failure_without_deliverer_does_not_crash(tmp_db: DbPool) -> None:
    sched = _sched(tmp_db, _AlwaysFailsHandler("morning_brief"), deliverer=None)
    job = _job("morning_brief")
    await insert_job(tmp_db, job)

    await _exhaust_retries(tmp_db, sched, job.job_id)

    rows = await tmp_db.fetch_all(
        "SELECT status FROM jobs WHERE job_id = ?", (job.job_id,)
    )
    # Lifecycle still completed (re-armed) with no deliverer wired.
    assert rows[0]["status"] == "pending"


async def test_notification_failure_does_not_break_lifecycle(tmp_db: DbPool) -> None:
    sched = _sched(
        tmp_db, _AlwaysFailsHandler("morning_brief"), deliverer=_RaisingDeliverer()
    )
    job = _job("morning_brief")
    await insert_job(tmp_db, job)

    await _exhaust_retries(tmp_db, sched, job.job_id)

    rows = await tmp_db.fetch_all(
        "SELECT status FROM jobs WHERE job_id = ?", (job.job_id,)
    )
    # A notify exception must never abort the durable re-arm write.
    assert rows[0]["status"] == "pending"


# ---------------------------------------------------------------------------
# F-62 — missing handler leaves the job recoverable, not terminally failed
# ---------------------------------------------------------------------------


async def test_missing_handler_leaves_job_pending(tmp_db: DbPool) -> None:
    sched = _sched(tmp_db, None)  # no handler registered
    job = _job("late_handler", params={"run_once": True})  # even a one-shot
    await insert_job(tmp_db, job)

    await sched._poll()

    rows = await tmp_db.fetch_all(
        "SELECT status, retry_count FROM jobs WHERE job_id = ?", (job.job_id,)
    )
    assert rows[0]["status"] == "pending", (
        "a missing handler must NOT mark the job failed — it must stay recoverable"
    )
    assert int(rows[0]["retry_count"]) == 0, (
        "a registration gap is not a handler failure — retry_count must not advance"
    )


async def test_missing_handler_recovers_once_registered(tmp_db: DbPool) -> None:
    sched = _sched(tmp_db, None)
    job = _job("late_handler")
    await insert_job(tmp_db, job)

    await sched._poll()  # handler missing — left pending
    rows = await tmp_db.fetch_all(
        "SELECT status FROM jobs WHERE job_id = ?", (job.job_id,)
    )
    assert rows[0]["status"] == "pending"

    # Handler registers later; re-due the (still-pending) row and poll again.
    handler = _OkHandler("late_handler")
    HandlerRegistry.instance().register(handler)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    await tmp_db.execute(
        "UPDATE jobs SET next_run_at = ? WHERE job_id = ?", (past, job.job_id)
    )
    await sched._poll()

    assert handler.calls == 1, "the job must run once its handler finally registers"
