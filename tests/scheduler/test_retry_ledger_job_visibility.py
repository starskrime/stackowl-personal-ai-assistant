"""PATHFINDER-2026-07-22 Proposal 5 (part 2) — a scheduled job's own provider
calls (evolution, critic_scorer, tool_outcome_miner — handlers that never
construct a PipelineState / never call backend.run()) previously had NO
retry_ledger binding at all: a circuit-breaker-open during one of those jobs
was invisible everywhere. _run_job now binds retry_ledger around every
handler dispatch (the ONE central point every handler funnels through),
mirroring the turn-level "[retry] turn summary" pattern.

Mirrors test_handler_timeout.py's harness shape exactly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra import retry_ledger
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.scheduler.scheduler_helpers import insert_job

pytestmark = pytest.mark.asyncio


class _RecordsRetryEventHandler(JobHandler):
    """A handler whose execute() calls a provider that hits a circuit-open —
    mirrors owls/evolution.py's _llm_fallback -> safe_complete ->
    _resilient_round -> retry_ledger.record_retry() call chain, without
    needing a real provider/circuit breaker."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def handler_name(self) -> str:
        return self._name

    @property
    def trigger_kind(self) -> str:  # type: ignore[override]
        return "on_demand"

    async def execute(self, job: Job) -> JobResult:
        # The real thing this proves: retry_ledger is BOUND at this point
        # (record_retry is a no-op, logged "unbound turn", when it isn't).
        retry_ledger.record_retry(kind="circuit_open_skip", provider="powerful-main", detail="OPEN")
        return JobResult(job_id=job.job_id, success=True, output="ok", duration_ms=1.0)


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


async def test_handler_provider_retry_event_is_captured_and_logged(
    tmp_db: DbPool, caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    reg = HandlerRegistry.instance()
    handler = _RecordsRetryEventHandler("evolution_batch")
    reg.register(handler)
    sched = JobScheduler(db=tmp_db, handler_registry=reg)

    job = _job("evolution_batch")
    await insert_job(tmp_db, job)

    with caplog.at_level(logging.INFO, logger="stackowl.heartbeat"):
        await sched._poll()

    summary_lines = [r for r in caplog.records if r.getMessage() == "[retry] job summary"]
    assert len(summary_lines) == 1
    events = summary_lines[0]._fields["events"]  # type: ignore[attr-defined]
    assert events == [{
        "kind": "circuit_open_skip", "provider": "powerful-main",
        "detail": "OPEN", "attempt_number": None,
    }]

    # The bind is turn/job-scoped — nothing leaks into the ambient (unbound)
    # context once the job finishes.
    assert retry_ledger.get_retry() == ()


async def test_handler_with_no_retry_events_logs_nothing(
    tmp_db: DbPool, caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    class _QuietHandler(JobHandler):
        @property
        def handler_name(self) -> str:
            return "quiet_job"

        @property
        def trigger_kind(self) -> str:  # type: ignore[override]
            return "on_demand"

        async def execute(self, job: Job) -> JobResult:
            return JobResult(job_id=job.job_id, success=True, output="ok", duration_ms=1.0)

    reg = HandlerRegistry.instance()
    reg.register(_QuietHandler())
    sched = JobScheduler(db=tmp_db, handler_registry=reg)

    job = _job("quiet_job")
    await insert_job(tmp_db, job)

    with caplog.at_level(logging.INFO, logger="stackowl.heartbeat"):
        await sched._poll()

    assert not [r for r in caplog.records if r.getMessage() == "[retry] job summary"]
