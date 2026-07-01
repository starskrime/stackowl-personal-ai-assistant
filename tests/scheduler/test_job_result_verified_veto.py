"""PB6a — the scheduler veto: `success=True, verified=False` must NOT reach
`_mark_completed`. It routes through the same retry/terminal-fail path as an
ordinary `success=False` result, via `is_trustworthy_success` (the same
predicate `ToolResult` callers already use). `verified=None` (the default —
every un-migrated handler) and `verified=True` are byte-identical to the
pre-existing success path.
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


class _FixedResultHandler(JobHandler):
    """A handler that always returns a pre-set `JobResult` — lets a test drive
    every `success`/`verified` combination through the real dispatch path."""

    def __init__(self, name: str, result_kwargs: dict[str, Any]) -> None:
        self._name = name
        self._result_kwargs = result_kwargs
        self.calls = 0

    @property
    def handler_name(self) -> str:
        return self._name

    @property
    def trigger_kind(self) -> str:  # type: ignore[override]
        return "on_demand"

    async def execute(self, job: Job) -> JobResult:
        self.calls += 1
        return JobResult(job_id=job.job_id, duration_ms=1.0, **self._result_kwargs)


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


def _sched(db: DbPool, handler: JobHandler) -> JobScheduler:
    reg = HandlerRegistry.instance()
    reg.register(handler)
    return JobScheduler(db=db, handler_registry=reg)


async def _dispatch_once(
    db: DbPool, result_kwargs: dict[str, Any], *, monkeypatch: pytest.MonkeyPatch
) -> tuple[Job, JobHandler]:
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *_a, **_kw: None,
    )
    handler_name = f"h-{uuid.uuid4().hex[:6]}"
    handler = _FixedResultHandler(handler_name, result_kwargs)
    sched = _sched(db, handler)
    job = _job(handler_name, params={"run_once": True})
    await insert_job(db, job)
    await sched._poll()
    return job, handler


@pytest.mark.asyncio
async def test_verified_none_dispatches_completed(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard — un-migrated handlers (verified always None) keep the
    pre-existing byte-identical success path."""
    job, _handler = await _dispatch_once(
        tmp_db, dict(success=True, verified=None, output="ok", error=None), monkeypatch=monkeypatch
    )
    rows = await tmp_db.fetch_all(
        "SELECT status FROM job_runs WHERE job_id = ? AND status = 'completed'", (job.job_id,)
    )
    assert len(rows) == 1, "success=True, verified=None must reach _mark_completed"


@pytest.mark.asyncio
async def test_verified_true_dispatches_completed(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    job, _handler = await _dispatch_once(
        tmp_db, dict(success=True, verified=True, output="ok", error=None), monkeypatch=monkeypatch
    )
    rows = await tmp_db.fetch_all(
        "SELECT status FROM job_runs WHERE job_id = ? AND status = 'completed'", (job.job_id,)
    )
    assert len(rows) == 1, "success=True, verified=True must reach _mark_completed"


@pytest.mark.asyncio
async def test_verified_false_does_not_dispatch_completed(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The veto: a claimed success that was checked and found absent must NOT
    reach _mark_completed — it takes the retry/terminal-fail path instead."""
    job, handler = await _dispatch_once(
        tmp_db,
        dict(success=True, verified=False, output="ok", error="post-condition not observed"),
        monkeypatch=monkeypatch,
    )
    completed = await tmp_db.fetch_all(
        "SELECT status FROM job_runs WHERE job_id = ? AND status = 'completed'", (job.job_id,)
    )
    assert completed == [], "success=True, verified=False must NEVER reach _mark_completed"
    rows = await tmp_db.fetch_all("SELECT status FROM jobs WHERE job_id = ?", (job.job_id,))
    assert len(rows) == 1
    # run_once + first failure => retry path (not yet exhausted), same as an
    # ordinary success=False result would take.
    assert rows[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_verified_false_exhausts_to_terminal_fail_like_plain_failure(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Driven past max retries, a verified=False claimed-success job reaches the
    SAME terminal state (_mark_failed, one-shot => 'failed') a success=False
    result would — proving it takes the identical failure branch, not a new one.
    """
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *_a, **_kw: None,
    )
    handler_name = f"h-{uuid.uuid4().hex[:6]}"
    handler = _FixedResultHandler(
        handler_name, dict(success=True, verified=False, output="ok", error="not observed")
    )
    sched = _sched(tmp_db, handler)
    job = _job(handler_name, params={"run_once": True})
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
        rows = await tmp_db.fetch_all("SELECT status FROM jobs WHERE job_id = ?", (job.job_id,))
        if not rows or rows[0]["status"] == "failed":
            break

    rows = await tmp_db.fetch_all("SELECT status FROM jobs WHERE job_id = ?", (job.job_id,))
    assert len(rows) == 1
    assert rows[0]["status"] == "failed", "one-shot verified=False must reach terminal failed"
    completed = await tmp_db.fetch_all(
        "SELECT status FROM job_runs WHERE job_id = ? AND status = 'completed'", (job.job_id,)
    )
    assert completed == [], "must never have reached _mark_completed across the whole retry run"


@pytest.mark.asyncio
async def test_plain_failure_unchanged(tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard — success=False, verified=None (the existing shape) still
    takes the retry path unchanged."""
    job, _handler = await _dispatch_once(
        tmp_db, dict(success=False, verified=None, output=None, error="boom"), monkeypatch=monkeypatch
    )
    completed = await tmp_db.fetch_all(
        "SELECT status FROM job_runs WHERE job_id = ? AND status = 'completed'", (job.job_id,)
    )
    assert completed == []
    rows = await tmp_db.fetch_all("SELECT status FROM jobs WHERE job_id = ?", (job.job_id,))
    assert rows[0]["status"] == "pending"


def test_job_result_backward_compat_defaults() -> None:
    """Construction-level guard — the pre-existing 6-field call shape (and the
    zero-arg-except-required shape) still construct, with the new fields
    defaulting to the byte-identical-behavior values."""
    r1 = JobResult(job_id="j1", success=True, output=None, error=None, duration_ms=1.0)
    assert r1.verified is None
    assert r1.effect_class == "state_change"
    assert r1.post_condition is None
