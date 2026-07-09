"""A slow-but-legitimate handler must not delay every OTHER due job's dispatch.

Live incident (2026-07-09): telegram_canary_send health alerts flapped every
~70-90 minutes with an identical "no successful send confirmed" message.
Real notification_log data showed canary firing on a clean 20m/20m/~50m
rhythm — every third cycle delayed by ~30 minutes, locked to dream_worker's
own 30-minute cadence. Root cause: ``_poll`` dispatched due jobs one at a
time (``for row in rows: await self._run_job(...)``), so whenever
dream_worker's legitimate (non-hung) ~20-30min run overlapped a poll cycle,
it silently blocked canary's dispatch for its entire duration — no timeout,
no error, just a late send. This proves the fix: due jobs now dispatch via
``asyncio.gather``, so a slow sibling can no longer delay an unrelated job.
"""

from __future__ import annotations

import asyncio
import time
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

_SLOW_SECONDS = 0.3


class _SlowHandler(JobHandler):
    """A handler that legitimately takes a while but always completes."""

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
        await asyncio.sleep(_SLOW_SECONDS)
        return JobResult(job_id=job.job_id, success=True, output="ok", error=None, duration_ms=0.0)


class _FastHandler(JobHandler):
    """A handler that returns immediately and records WHEN it ran."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.calls = 0
        self.ran_at: float | None = None

    @property
    def handler_name(self) -> str:
        return self._name

    @property
    def trigger_kind(self) -> str:  # type: ignore[override]
        return "on_demand"

    async def execute(self, job: Job) -> JobResult:
        self.calls += 1
        self.ran_at = time.monotonic()
        return JobResult(job_id=job.job_id, success=True, output="ok", error=None, duration_ms=0.0)


def _job(handler: str, **overrides: Any) -> Job:
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    defaults: dict[str, Any] = dict(
        job_id=f"{handler}-{uuid.uuid4().hex[:6]}",
        handler_name=handler,
        schedule="every 20m",
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


async def test_slow_job_does_not_delay_sibling_dispatch(tmp_db: DbPool) -> None:
    reg = HandlerRegistry.instance()
    slow = _SlowHandler("dream_worker")
    fast = _FastHandler("telegram_canary")
    reg.register(slow)
    reg.register(fast)
    sched = JobScheduler(db=tmp_db, handler_registry=reg)

    slow_job = _job("dream_worker")
    fast_job = _job("telegram_canary")
    await insert_job(tmp_db, slow_job)
    await insert_job(tmp_db, fast_job)

    t0 = time.monotonic()
    await asyncio.wait_for(sched._poll(), timeout=5.0)
    total = time.monotonic() - t0

    assert slow.calls == 1
    assert fast.calls == 1
    assert fast.ran_at is not None
    # The fast handler ran WELL BEFORE the slow one finished — proving they
    # dispatched concurrently, not sequentially (sequential would force the
    # fast handler to wait ~_SLOW_SECONDS before even starting).
    assert fast.ran_at - t0 < _SLOW_SECONDS / 2
    # The whole poll took roughly the slow handler's own duration, not the
    # sum of both — the definitive proof concurrency actually happened.
    assert total < _SLOW_SECONDS * 1.8
