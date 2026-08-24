"""ESC-53 — a one-shot job must not die permanently on a fault that will pass.

BAKIR'S DECISION, 2026-08-24: "Re-arm one-shots too." Extend the 2026-07-22
never-give-up rule to one-shots — on a TRANSIENT failure re-queue with backoff
instead of dying terminally; genuine permanent faults still terminate.

WHAT IT COST TO LEARN. Measured on the live jobs table: 72 of 106 rows were
`rollover_summary`, 63 of them terminal `failed`, all at retry_count=2. FIFTY-FOUR
died on "All providers unavailable: NeraAiRaw: skipped (circuit open)" across 16
distinct days between 2026-07-27 and 2026-08-20. A rollover summary is how a
conversation's content survives the end of its window; those 54 were never written
and are queued nowhere. The provider was down for minutes and the work was lost
for good.

THE ASYMMETRY THAT CAUSED IT. `_mark_failed` re-arms a RECURRING job onto its next
cadence slot — that is F-60, and its comment is explicit that "the job itself is
never given up on". A ONE-SHOT took the terminal branch. So whether work survived
a transient blip was decided by CADENCE, which has nothing to do with whether the
work still needs doing.

WHY THE CLASSIFIER HAD TO CHANGE TOO. Measured before writing this: the shared
`classify_failure` returns "" (unknown, therefore retryable) for BOTH
  "summary call failed: All providers unavailable ... (circuit open)"   <- must retry
  "malformed job: session_key and ended_conversation_id are required"   <- must NOT
so it could not tell the two apart. The malformed nine were created without the
fields their handler requires and could never have succeeded once; re-arming them
forever would be the no-decay failure mode wearing a fix's clothing.

ONE COPY OF THE RULE. "Which classes are permanent" already had a home and a
deployment override (`settings.task_loop.permanent_failure_classes`). It was
private to the durable store. It now lives beside `wants_reshaping` in the leaf
classifier that exists precisely so this vocabulary has ONE home, and both the
durable loop and the scheduler ask it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.failure_class import (
    classify_failure,
    is_permanent,
    permanent_classes,
)
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.scheduler.scheduler import _MAX_RETRIES, JobScheduler
from stackowl.scheduler.scheduler_helpers import insert_job

#: Verbatim from the 54 lost rows.
CIRCUIT_OPEN = (
    "summary call failed: All providers unavailable: NeraAiRaw: skipped "
    "(circuit open); NeraAiRaw: skipped (circuit open)"
)
#: Verbatim from the 9 that could never have worked.
MALFORMED = "malformed job: session_key and ended_conversation_id are required"


class _FailsWith(JobHandler):
    def __init__(self, name: str, error: str) -> None:
        self._name = name
        self._error = error
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
            job_id=job.job_id, success=False, output=None,
            error=self._error, duration_ms=1.0,
        )


def _one_shot(handler: str, **overrides: Any) -> Job:
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    defaults: dict[str, Any] = dict(
        job_id=f"{handler}-{uuid.uuid4().hex[:6]}",
        handler_name=handler,
        schedule="once",
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


def _sched(db: DbPool, handler: JobHandler) -> JobScheduler:
    reg = HandlerRegistry.instance()
    reg.register(handler)
    return JobScheduler(db=db, handler_registry=reg)


async def _drive_to_exhaustion(db: DbPool, sched: JobScheduler, job_id: str) -> None:
    """Burn the within-occurrence retries until _mark_failed's branch is REACHED.

    Stops on terminal `failed` OR on the first re-arm. The stop condition matters:
    the within-occurrence retry path sets `retry_at` and deliberately never
    touches `next_run_at` ("NEVER touch next_run_at — the canonical recurring
    cadence"), so polling past the re-arm would leave the row back in that state
    with the past `next_run_at` this helper forced, and an assertion about the
    re-arm would be reading the wrong transition.
    """
    for _ in range(_MAX_RETRIES + 2):
        rows = await db.fetch_all(
            "SELECT status, failure_count FROM jobs WHERE job_id = ?", (job_id,)
        )
        if not rows or rows[0]["status"] == "failed":
            return
        if (rows[0]["failure_count"] or 0) >= 1:
            return  # a re-arm has landed — that is the transition under test
        await db.execute(
            "UPDATE jobs SET next_run_at = ?, retry_at = NULL WHERE job_id = ? "
            "AND status = 'pending'",
            ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), job_id),
        )
        await sched._poll()


# ---------------------------------------------------------------------------
# The classifier must first be able to tell the two apart
# ---------------------------------------------------------------------------

def test_the_two_real_error_strings_classify_differently() -> None:
    """Before the fix BOTH returned "" — the whole reason the scheduler could not
    discriminate. Fixtures are the live strings, not invented ones."""
    assert not is_permanent(classify_failure(CIRCUIT_OPEN)), (
        "a provider circuit that is open now will not be open forever"
    )
    assert is_permanent(classify_failure(MALFORMED)), (
        "a job created without the fields its handler requires can never succeed, "
        "so re-arming it forever would just be a slower way to lose"
    )


def test_an_unknown_failure_still_defaults_to_RETRYABLE() -> None:
    """The classifier's load-bearing default, documented in its own module: a wrong
    'permanent' strands work that would have succeeded, a wrong 'retryable' only
    spends attempts that backoff already bounds."""
    assert not is_permanent(classify_failure("something nobody has seen before"))
    assert not is_permanent(classify_failure(""))
    assert not is_permanent(classify_failure(None))


def test_the_permanent_set_has_ONE_home() -> None:
    """It was private to the durable store. The scheduler needed the same answer,
    and the second copy is the shape this codebase keeps paying to fix."""
    from stackowl.pipeline.durable import store

    assert store._permanent_classes() == permanent_classes(), (
        "the durable loop and the scheduler must be asking the SAME rule"
    )


# ---------------------------------------------------------------------------
# The behaviour ESC-53 is about
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_one_shot_that_failed_transiently_is_re_queued(
    tmp_db: DbPool,
) -> None:
    """The defect: this row used to end up terminal `failed`, and its work gone."""
    handler = _FailsWith("rollover_summary", CIRCUIT_OPEN)
    sched = _sched(tmp_db, handler)
    job = _one_shot("rollover_summary")
    await insert_job(tmp_db, job)

    await _drive_to_exhaustion(tmp_db, sched, job.job_id)

    row = (await tmp_db.fetch_all(
        "SELECT status, next_run_at, failure_count, last_error FROM jobs "
        "WHERE job_id = ?", (job.job_id,),
    ))[0]
    assert row["status"] == "pending", (
        "a one-shot that failed on an open provider circuit must live to try "
        "again — 54 rollover summaries were lost exactly here"
    )
    assert row["failure_count"] >= 1, "the re-arm must count, or backoff cannot grow"
    assert row["last_error"], "and it must still say WHY it is waiting"


@pytest.mark.asyncio
async def test_the_re_queue_is_in_the_FUTURE_not_a_hot_loop(
    tmp_db: DbPool,
) -> None:
    """Backoff is what makes 'never give up' safe. Without it a permanently-sick
    one-shot would spin on every poll."""
    handler = _FailsWith("rollover_summary", CIRCUIT_OPEN)
    sched = _sched(tmp_db, handler)
    job = _one_shot("rollover_summary")
    await insert_job(tmp_db, job)

    before = datetime.now(UTC)
    await _drive_to_exhaustion(tmp_db, sched, job.job_id)

    row = (await tmp_db.fetch_all(
        "SELECT next_run_at FROM jobs WHERE job_id = ?", (job.job_id,)
    ))[0]
    assert datetime.fromisoformat(row["next_run_at"]) > before, (
        "the retry must be scheduled forward in time, not left due immediately"
    )


@pytest.mark.asyncio
async def test_a_PERMANENT_failure_still_terminates(tmp_db: DbPool) -> None:
    """Bakir's contract names this explicitly: genuine permanent faults still
    terminate. The nine malformed rollover jobs could never have succeeded."""
    handler = _FailsWith("rollover_summary", MALFORMED)
    sched = _sched(tmp_db, handler)
    job = _one_shot("rollover_summary")
    await insert_job(tmp_db, job)

    await _drive_to_exhaustion(tmp_db, sched, job.job_id)

    row = (await tmp_db.fetch_all(
        "SELECT status FROM jobs WHERE job_id = ?", (job.job_id,)
    ))[0]
    assert row["status"] == "failed", (
        "re-arming a job that can never succeed is not persistence, it is the "
        "no-decay failure mode with better manners"
    )


@pytest.mark.asyncio
async def test_a_RECURRING_job_is_completely_unaffected(tmp_db: DbPool) -> None:
    """F-60 must keep behaving exactly as it did — this change is scoped to the
    branch that used to be terminal."""
    handler = _FailsWith("morning_brief", CIRCUIT_OPEN)
    sched = _sched(tmp_db, handler)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    job = Job(
        job_id=f"morning_brief-{uuid.uuid4().hex[:6]}",
        handler_name="morning_brief",
        schedule="daily@08:00",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=past,
        status="pending",
        params={},
    )
    await insert_job(tmp_db, job)

    await _drive_to_exhaustion(tmp_db, sched, job.job_id)

    row = (await tmp_db.fetch_all(
        "SELECT status FROM jobs WHERE job_id = ?", (job.job_id,)
    ))[0]
    assert row["status"] == "pending", "recurring re-arm (F-60) must be untouched"
