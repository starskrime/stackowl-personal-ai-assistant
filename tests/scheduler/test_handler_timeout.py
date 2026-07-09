"""A hung handler must not freeze the scheduler forever.

Live incident (2026-07-08): dream_worker's execute() hung mid-run (a stuck
network call to the LLM provider). ``_run_job`` awaited it with no timeout —
back when ``_poll`` still dispatched jobs sequentially (``for row in rows:
await self._run_job(...)``), the hang froze dispatch of every OTHER due job
too, including a user's one-shot reminder that sat ready in the ``jobs``
table for 45+ minutes until the process was manually restarted. This proves
the fix: ``asyncio.wait_for`` bounds any handler's execution so one hang can
never starve the rest of the schedule again. ``_poll`` was later (2026-07-09)
also switched to concurrent dispatch (see ``test_concurrent_dispatch.py``) so
even a slow-but-not-hung handler can't delay siblings either.
"""

from __future__ import annotations

import asyncio
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


class _HangsForeverHandler(JobHandler):
    """A handler whose execute() never returns on its own — must be timed out."""

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
        await asyncio.Event().wait()  # never set — hangs until cancelled
        raise AssertionError("unreachable — must be cancelled by the timeout")


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


async def test_hung_handler_times_out_instead_of_freezing_dispatch(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stackowl.scheduler.scheduler as scheduler_mod

    monkeypatch.setattr(scheduler_mod, "_HANDLER_TIMEOUT_SEC", 0.05)

    reg = HandlerRegistry.instance()
    handler = _HangsForeverHandler("dream_worker")
    reg.register(handler)
    sched = JobScheduler(db=tmp_db, handler_registry=reg)

    hung_job = _job("dream_worker")
    await insert_job(tmp_db, hung_job)

    # Must return promptly (bounded by the patched timeout), not hang the test.
    await asyncio.wait_for(sched._poll(), timeout=5.0)

    assert handler.calls == 1
    rows = await tmp_db.fetch_all(
        "SELECT status, last_error FROM jobs WHERE job_id = ?", (hung_job.job_id,)
    )
    # Recurring (F-60) — re-armed to pending for its next slot, never stuck.
    assert rows[0]["status"] == "pending"
