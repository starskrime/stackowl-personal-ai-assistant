"""An overdue ONE-SHOT job must replay on recover(), never be silently deferred.

Live incident (2026-07-08): a cronjob "in 5m" reminder sat overdue with
``replay_missed=False`` (the default). Every platform restart's ``recover()``
took the "just reschedule" branch, recomputing the RELATIVE "in 5m" schedule
fresh from that restart's boot time — pushing the reminder further into the
future without ever firing it. A recurring job losing a missed occurrence is
benign (it fires again soon); a one-shot losing it is the user's request
silently vanishing.
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


class _RecordingHandler(JobHandler):
    def __init__(self, name: str) -> None:
        self._name = name
        self.calls: list[str] = []

    @property
    def handler_name(self) -> str:
        return self._name

    @property
    def trigger_kind(self) -> str:  # type: ignore[override]
        return "on_demand"

    async def execute(self, job: Job) -> JobResult:
        self.calls.append(job.job_id)
        return JobResult(job_id=job.job_id, success=True, output="ok", error=None, duration_ms=1.0)


def _job(handler: str, **overrides: Any) -> Job:
    past = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    defaults: dict[str, Any] = dict(
        job_id=f"{handler}-{uuid.uuid4().hex[:6]}",
        handler_name=handler,
        schedule="in 5m",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=past,
        status="pending",
        replay_missed=False,
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


async def test_overdue_one_shot_replays_on_recover_even_without_replay_missed(
    tmp_db: DbPool,
) -> None:
    reg = HandlerRegistry.instance()
    handler = _RecordingHandler("goal_execution")
    reg.register(handler)
    sched = JobScheduler(db=tmp_db, handler_registry=reg)

    job = _job("goal_execution")
    await insert_job(tmp_db, job)

    await sched.recover()

    assert job.job_id in handler.calls, "overdue one-shot must replay, not be silently deferred"


async def test_overdue_recurring_job_without_replay_missed_just_reschedules(
    tmp_db: DbPool,
) -> None:
    reg = HandlerRegistry.instance()
    handler = _RecordingHandler("telegram_canary")
    reg.register(handler)
    sched = JobScheduler(db=tmp_db, handler_registry=reg)

    job = _job("telegram_canary", schedule="every 20m", params={})
    await insert_job(tmp_db, job)

    await sched.recover()

    assert job.job_id not in handler.calls, "recurring behavior unchanged — reschedules quietly"
    rows = await tmp_db.fetch_all(
        "SELECT next_run_at FROM jobs WHERE job_id = ?", (job.job_id,)
    )
    assert datetime.fromisoformat(rows[0]["next_run_at"]) > datetime.now(UTC)
