"""A daily job that misses its slot must not lose the day.

MEASURED 2026-09-08, on one boot of this platform — `recover()` at
2026-09-08T02:29, after the 7h21m silent stall:

    02:29:58  recover: replaying missed job   goal_execution-88f54aa1
              (recurring=true, replay_missed=true)
    ...       check_in-df94ceab               overdue 3h29m, advanced in silence

Two overdue `daily@` jobs, one `recover()` call, opposite outcomes. The
difference was a per-job boolean: `tools/scheduling/cronjob.py` sets
`replay_missed=True` on the jobs IT creates, and the scheduler assembly that
seeds the platform's own jobs never passes the field at all — so it takes
`Job.replay_missed`'s dataclass default of False. Live counts: of 15 enabled
`daily@` jobs, **3 carry the flag and 12 do not**, and the twelve are the
platform's own — the morning brief, the check-in, the nightly prune, the profile
backup. `check_in` and `owl_lifecycle-jobmarket` each lost their 2026-09-07
occurrence that way and nothing recorded it.

THE SCALE OF THE SILENCE. Across 749 `recover: exit` records the scheduler saw
**1,553 overdue occurrences and replayed 10** — 0.6%. The other 1,543 were
advanced with no log line at all, because only the replay branch logs. Work the
platform owed simply stopped existing, and no instrument could say so.

WHY IT HAPPENED, and it is not the flag. The belief is written down twice, in
`scheduler.py`'s own comment ("unlike a recurring job which gets another
occurrence soon regardless") and in
`test_recover_one_shot_replay.py`'s docstring ("A recurring job losing a missed
occurrence is benign (it fires again soon)"). That is TRUE of `every 20m` and
FALSE of `daily@`: the rule was formed on the frequent case and applied to the
whole class. Expressing it as a per-job flag then made it something each creation
site had to remember, and one of the two never did.

So the policy is DERIVED from the schedule here rather than declared per job.
`schedule_interval_seconds` already knows every accepted form and already exists
— it moved to `scheduler_helpers` beside `compute_next_run` so the scheduler and
the owl guard ask ONE implementation instead of two.

THE BOUNDARY IS ONE DAY, and it is a judgement stated rather than hidden. Every
dropped slot costs exactly one occurrence; what differs is what fraction of the
job's work that is. At `every 20m` it is one of 72 and the job runs again within
the hour. At `daily@` it is all of it, and the operator-visible fact is that the
job DOES NOT RUN AGAIN TODAY. That is the line.

`replay_missed` is not retired by this: it still WIDENS the rule, forcing a
replay for a job that recurs faster than a day, and `cronjob.py` still sets it.
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
        return "scheduled"

    async def execute(self, job: Job) -> JobResult:
        self.calls.append(job.job_id)
        return JobResult(
            job_id=job.job_id, success=True, output="ok", error=None, duration_ms=1.0
        )


def _overdue_job(handler: str, schedule: str, *, overdue: timedelta, **over: Any) -> Job:
    """A pending recurring job whose slot passed `overdue` ago.

    `params={}` — NOT `run_once` — on purpose: the one-shot branch replays
    unconditionally and would satisfy every assertion below without the derived
    rule existing at all, which is the "satisfied by a case the old code handled
    too" trap this repo pays for most.
    """
    defaults: dict[str, Any] = dict(
        job_id=f"{handler}-{uuid.uuid4().hex[:6]}",
        handler_name=handler,
        schedule=schedule,
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=(datetime.now(UTC) - overdue).isoformat(),
        status="pending",
        replay_missed=False,
        params={},
    )
    defaults.update(over)
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


async def _recover_with(tmp_db: DbPool, job: Job, handler_name: str) -> _RecordingHandler:
    reg = HandlerRegistry.instance()
    handler = _RecordingHandler(handler_name)
    reg.register(handler)
    await insert_job(tmp_db, job)
    await JobScheduler(db=tmp_db, handler_registry=reg).recover()
    return handler


async def test_the_check_in_that_was_lost_is_replayed(tmp_db: DbPool) -> None:
    """THE LIVE CASE. `daily@18:00`, overdue 3h29m, `replay_missed=0`."""
    job = _overdue_job("check_in", "daily@18:00", overdue=timedelta(hours=3, minutes=29))
    handler = await _recover_with(tmp_db, job, "check_in")

    assert job.job_id in handler.calls, (
        "a daily job overdue by hours was advanced to tomorrow instead of run — "
        "the operator's check-in for that day simply never happened"
    )


async def test_a_frequent_job_that_has_moved_on_is_still_dropped(tmp_db: DbPool) -> None:
    """THE OTHER HALF, and the reason this is not 'replay everything'.

    An `every 20m` job overdue by three hours has had nine slots come and go. Its
    work is fungible across them; running the stale one buys nothing and every
    boot on this box would pay for it.
    """
    job = _overdue_job("notification_digest", "every 20m", overdue=timedelta(hours=3))
    handler = await _recover_with(tmp_db, job, "notification_digest")

    assert job.job_id not in handler.calls, (
        "a job that recurs every 20 minutes was replayed for a slot three hours "
        "stale — the derived rule has become 'replay everything'"
    )
    rows = await tmp_db.fetch_all(
        "SELECT next_run_at FROM jobs WHERE job_id = ?", (job.job_id,)
    )
    assert datetime.fromisoformat(rows[0]["next_run_at"]) > datetime.now(UTC)


async def test_the_rule_reads_the_PERIOD_not_the_schedule_spelling(tmp_db: DbPool) -> None:
    """A five-minute CRON must be treated as a five-minute job.

    The tempting shortcut is the schedule FORM — `daily@`/cron replays, `every N`
    does not. `*/5 * * * *` is cron-form and fires every five minutes, so that
    rule would replay it. Asking `schedule_interval_seconds` for the period
    cannot make that mistake.
    """
    job = _overdue_job("tool_revalidation", "*/5 * * * *", overdue=timedelta(hours=2))
    handler = await _recover_with(tmp_db, job, "tool_revalidation")

    assert job.job_id not in handler.calls, (
        "a cron firing every five minutes was replayed because it is cron-SHAPED; "
        "the decision must come from the interval, not the spelling"
    )


async def test_a_daily_cron_is_replayed_like_a_daily_at(tmp_db: DbPool) -> None:
    """The mirror of the test above — same reasoning, opposite outcome."""
    job = _overdue_job("morning_brief", "0 8 * * *", overdue=timedelta(hours=2))
    handler = await _recover_with(tmp_db, job, "morning_brief")

    assert job.job_id in handler.calls, (
        "a once-a-day cron lost its occurrence; only the `daily@` spelling was fixed"
    )


async def test_the_flag_still_widens_the_rule(tmp_db: DbPool) -> None:
    """`replay_missed` is NOT retired by deriving the default.

    `cronjob.py` sets it on every job it creates, and it still means what it
    said: replay this one even though its cadence would not have earned it.
    """
    job = _overdue_job(
        "goal_execution", "every 20m", overdue=timedelta(hours=3), replay_missed=True
    )
    handler = await _recover_with(tmp_db, job, "goal_execution")

    assert job.job_id in handler.calls, (
        "an explicit replay_missed=True no longer forces a replay — the override "
        "was silently swallowed by the derived default"
    )


async def test_a_dropped_occurrence_is_no_longer_silent(
    tmp_db: DbPool, caplog: pytest.LogCaptureFixture
) -> None:
    """1,543 occurrences were advanced with NO log line, because only the replay
    branch logged. A drop is a decision about the operator's work and has to be
    as visible as the replay is."""
    import logging

    caplog.set_level(logging.INFO)
    job = _overdue_job("notification_digest", "every 20m", overdue=timedelta(hours=3))
    await _recover_with(tmp_db, job, "notification_digest")

    dropped = [r for r in caplog.records if "recover: missed occurrence dropped" in r.message]
    assert dropped, (
        "the scheduler advanced a missed occurrence and said nothing; no "
        "instrument can distinguish 'ran' from 'was skipped'"
    )


async def test_work_lost_outside_the_window_is_reported_as_lost(
    tmp_db: DbPool, caplog: pytest.LogCaptureFixture
) -> None:
    """A daily job overdue by MORE than the replay window is real data loss.

    It is correctly not replayed — a two-day-old brief is not worth sending — but
    it is the one case where the platform knowingly drops work it owed, so it
    must say so at WARNING rather than at INFO beside the benign drops.
    """
    import logging

    caplog.set_level(logging.INFO)
    job = _overdue_job("morning_brief", "daily@08:00", overdue=timedelta(hours=40))
    handler = await _recover_with(tmp_db, job, "morning_brief")

    assert job.job_id not in handler.calls
    lost = [
        r for r in caplog.records
        if "recover: missed occurrence dropped" in r.message
        and r.levelno >= logging.WARNING
    ]
    assert lost, (
        "a whole day's occurrence fell outside the replay window and was dropped "
        "at INFO, indistinguishable from a 20-minute job skipping a slot"
    )
