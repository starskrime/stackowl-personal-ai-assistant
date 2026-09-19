"""Story 3.4 (AC3) — a real scheduler-dispatched job completes an ordinary
consequential action, proving ``_bind_job_trace`` sets the EXPLICIT
``autonomous:scheduler`` principal rather than it being inferred from the
job's channel.

Drives the REAL dispatch path (``JobScheduler._run_job``, not a bare handler
call): the handler calls a real consequential tool through the real
``ConsequentialActionGate``/``ConsentPolicy(prompter=RoutingPrompter())`` with
NO prompter registered anywhere, and no channel wired either. Under the OLD
contract this only completed because ``RoutingPrompter`` fell back to
``AutonomousPrompter`` for ANY unwired channel; that fallback is deleted, so
this test only passes now because ``_bind_job_trace`` declared the principal
explicitly and ``ConsentPolicy.request()`` read it off ``TraceContext``
BEFORE ever calling ``RoutingPrompter``.

The job's own ``primary_channel`` is deliberately set to a channel name
("telegram") that LOOKS live but has no prompter registered for it either —
proving the grant follows the declared principal, never the channel's
liveness (spec Design Notes: "a job's channel can be a genuinely live, wired
channel ... yet no human is watching THIS specific automated trigger").
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.scheduler.scheduler_helpers import insert_job
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.consent import PRINCIPAL_AUTONOMOUS_SCHEDULER, ConsentPolicy, RoutingPrompter
from stackowl.tools.registry import ConsequentialActionGate

pytestmark = pytest.mark.asyncio


class _StubConsequentialTool(Tool):
    """An ordinary (non always-ask) consequential tool — mirrors
    ``tests/test_e0_s1_consent.py``'s own stub."""

    @property
    def name(self) -> str:
        return "send_file"

    @property
    def description(self) -> str:
        return "A consequential stub."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name, description=self.description,
            parameters=self.parameters, action_severity="consequential",
            command_types=("messaging.send_file",),
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ran", duration_ms=1.0)


class _ConsentCheckingHandler(JobHandler):
    """Calls a real consequential tool through the real consent gate, with NO
    prompter registered anywhere and no channel wired — proving the grant
    comes from the job's declared principal, not from channel liveness."""

    def __init__(self) -> None:
        self.principal_seen: str | None = None
        self.allowed: bool | None = None

    @property
    def handler_name(self) -> str:
        return "consent_check"

    async def execute(self, job: Job) -> JobResult:
        self.principal_seen = TraceContext.get().get("principal")

        gate = ConsequentialActionGate(ConsentPolicy(prompter=RoutingPrompter()))
        allowed = await gate.check(
            _StubConsequentialTool(),
            channel="telegram",  # looks live; NO prompter is registered for it
            session_key=f"job:{job.job_id}",
        )
        self.allowed = allowed
        return JobResult(
            job_id=job.job_id, success=allowed,
            output="ok" if allowed else None,
            error=None if allowed else "denied", duration_ms=1.0,
        )


def _job(handler: str) -> Job:
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    return Job(
        job_id=f"{handler}-{uuid.uuid4().hex[:6]}", handler_name=handler,
        schedule="daily@08:00", idempotency_key=uuid.uuid4().hex,
        last_run_at=None, next_run_at=past, status="pending", params={},
    )


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


async def test_a_scheduled_job_completes_an_ordinary_consequential_action(
    tmp_db: DbPool,
) -> None:
    handler = _ConsentCheckingHandler()
    reg = HandlerRegistry.instance()
    reg.register(handler)
    sched = JobScheduler(db=tmp_db, handler_registry=reg)

    job = _job("consent_check")
    await insert_job(tmp_db, job)

    # Nobody's TraceContext carries the principal BEFORE dispatch — proves
    # _run_job/_bind_job_trace is what sets it, not test setup leaking it in.
    assert TraceContext.get().get("principal") is None

    await sched._run_job(job)

    assert handler.principal_seen == PRINCIPAL_AUTONOMOUS_SCHEDULER, (
        "_bind_job_trace must set the principal EXPLICITLY on every job run"
    )
    assert handler.allowed is True, (
        "an ordinary consequential action must complete for a scheduled job "
        "even with no prompter registered for any channel"
    )

    # A recurring job that SUCCEEDS re-arms to 'pending' at its next slot with
    # a clean retry_count/last_error — the job_runs history row is the
    # durable proof the run itself completed successfully.
    rows = await tmp_db.fetch_all(
        "SELECT status, retry_count, last_error FROM jobs WHERE job_id = ?",
        (job.job_id,),
    )
    assert rows[0]["status"] == "pending"
    assert rows[0]["retry_count"] == 0
    assert rows[0]["last_error"] is None
    run_rows = await tmp_db.fetch_all(
        "SELECT status FROM job_runs WHERE job_id = ?", (job.job_id,),
    )
    assert run_rows and run_rows[0]["status"] == "completed"


async def test_the_principal_is_gone_once_the_job_run_ends(tmp_db: DbPool) -> None:
    """`_bind_job_trace`'s token is reset like every other TraceContext scope
    — the principal must not leak into whatever runs next on this task."""
    handler = _ConsentCheckingHandler()
    reg = HandlerRegistry.instance()
    reg.register(handler)
    sched = JobScheduler(db=tmp_db, handler_registry=reg)

    job = _job("consent_check")
    await insert_job(tmp_db, job)

    await sched._run_job(job)

    assert TraceContext.get().get("principal") is None
