"""The 3 ``owl_schedule``-owned command types (Story 4.7): ``scheduling.
pause_owl_job``/``resume_owl_job`` (calling the IDENTICAL ``JobScheduler.
pause``/``.resume`` the pilot pair calls, under a distinct command type) and
``scheduling.set_owl_schedule`` (``JobScheduler.snooze``, new this story).

Proves the pause_owl_job/resume_owl_job pair is not a second implementation
of pause/resume — same mutator, same transaction shape, just a different
``context.command_type`` on the receipt/journal row — and that ``snooze``
gains the exact same AD-26 shape (idempotency receipt + mutation + a
``job.snoozed`` journal event in one transaction) plus its own captured
undo payload (the job's PRIOR ``next_run_at``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stackowl.commands.spec.context import CommandContext
from stackowl.db.pool import DbPool
from stackowl.scheduler.scheduler import JobScheduler
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def tmp_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "owl_pause_resume_snooze.db"
    seed_schema(db_path)
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def _make_job(scheduler: JobScheduler) -> str:
    job = await scheduler.create_job(handler_name="goal_execution", schedule="every 2h")
    return job.job_id


# --------------------------------------------------------------------------- pause_owl_job


async def test_pause_owl_job_calls_the_same_pause_and_records_job_paused(
    tmp_db: DbPool,
) -> None:
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)
    context = CommandContext(
        command_id="cmd-pause-owl-1", command_type="scheduling.pause_owl_job",
        requester_kind="owner",
    )

    await scheduler.pause(job_id, context=context)

    rows = await tmp_db.fetch_all("SELECT status, enabled FROM jobs WHERE job_id = ?", (job_id,))
    assert rows[0]["status"] == "failed"
    assert rows[0]["enabled"] == 0

    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.paused' AND target_id = ?", (job_id,),
    )
    assert len(events) == 1


async def test_resume_owl_job_calls_the_same_resume_and_records_job_resumed(
    tmp_db: DbPool,
) -> None:
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)
    await scheduler.pause(job_id)
    context = CommandContext(
        command_id="cmd-resume-owl-1", command_type="scheduling.resume_owl_job",
        requester_kind="owner",
    )

    await scheduler.resume(job_id, context=context)

    rows = await tmp_db.fetch_all("SELECT status, enabled FROM jobs WHERE job_id = ?", (job_id,))
    assert rows[0]["status"] == "pending"
    assert rows[0]["enabled"] == 1

    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.resumed' AND target_id = ?", (job_id,),
    )
    assert len(events) == 1


async def test_pause_job_and_pause_owl_job_are_independent_command_ids_on_the_same_mutator(
    tmp_db: DbPool,
) -> None:
    """cronjob's `scheduling.pause_job` and owl_schedule's `scheduling.
    pause_owl_job` never merge — two DIFFERENT command_ids for the SAME
    job, one under each command_type, both record independently."""
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)

    await scheduler.pause(
        job_id,
        context=CommandContext(
            command_id="cmd-cronjob-pause", command_type="scheduling.pause_job",
            requester_kind="owner",
        ),
    )
    await scheduler.resume(job_id)  # reset, direct call
    await scheduler.pause(
        job_id,
        context=CommandContext(
            command_id="cmd-owl-pause", command_type="scheduling.pause_owl_job",
            requester_kind="owner",
        ),
    )

    receipts = await tmp_db.fetch_all(
        "SELECT command_id, command_type FROM command_receipts ORDER BY command_id",
    )
    types_by_id = {r["command_id"]: r["command_type"] for r in receipts}
    assert types_by_id["cmd-cronjob-pause"] == "scheduling.pause_job"
    assert types_by_id["cmd-owl-pause"] == "scheduling.pause_owl_job"


# --------------------------------------------------------------------------- snooze


async def test_snooze_with_context_records_job_snoozed_exactly_once(tmp_db: DbPool) -> None:
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)
    until_iso = (datetime.now(UTC) + timedelta(hours=8)).isoformat()
    context = CommandContext(
        command_id="cmd-snooze-1", command_type="scheduling.set_owl_schedule",
        requester_kind="owner",
    )

    await scheduler.snooze(job_id, until_iso, context=context)

    rows = await tmp_db.fetch_all(
        "SELECT status, enabled, next_run_at FROM jobs WHERE job_id = ?", (job_id,),
    )
    assert rows[0]["status"] == "pending"
    assert rows[0]["enabled"] == 1
    assert rows[0]["next_run_at"] == until_iso

    events = await tmp_db.fetch_all(
        "SELECT attrs FROM journal_events WHERE type = 'job.snoozed' AND target_id = ?",
        (job_id,),
    )
    assert len(events) == 1
    attrs = json.loads(events[0]["attrs"])
    assert attrs["until"] == until_iso


async def test_snooze_captures_the_prior_next_run_at_as_undo_payload(tmp_db: DbPool) -> None:
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)
    before = await tmp_db.fetch_all("SELECT next_run_at FROM jobs WHERE job_id = ?", (job_id,))
    prior_next_run_at = before[0]["next_run_at"]
    until_iso = (datetime.now(UTC) + timedelta(hours=8)).isoformat()
    context = CommandContext(
        command_id="cmd-snooze-undo", command_type="scheduling.set_owl_schedule",
        requester_kind="owner",
    )

    await scheduler.snooze(job_id, until_iso, context=context)

    rows = await tmp_db.fetch_all(
        "SELECT undo_payload FROM command_receipts WHERE command_id = ?", ("cmd-snooze-undo",),
    )
    assert len(rows) == 1
    captured = json.loads(rows[0]["undo_payload"])
    assert captured == {"job_id": job_id, "until": prior_next_run_at}


async def test_snooze_re_run_with_the_same_command_id_is_a_no_op(tmp_db: DbPool) -> None:
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)
    until_iso = (datetime.now(UTC) + timedelta(hours=8)).isoformat()
    context = CommandContext(
        command_id="cmd-snooze-reclaim", command_type="scheduling.set_owl_schedule",
        requester_kind="owner",
    )

    await scheduler.snooze(job_id, until_iso, context=context)
    other_until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    await tmp_db.execute(
        "UPDATE jobs SET next_run_at = ? WHERE job_id = ?", (other_until, job_id),
    )
    await scheduler.snooze(job_id, until_iso, context=context)

    rows = await tmp_db.fetch_all("SELECT next_run_at FROM jobs WHERE job_id = ?", (job_id,))
    assert rows[0]["next_run_at"] == other_until  # the re-run did not touch it

    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.snoozed' AND target_id = ?", (job_id,),
    )
    assert len(events) == 1


async def test_snooze_context_none_keeps_the_pre_4_7_behavior(tmp_db: DbPool) -> None:
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)
    until_iso = (datetime.now(UTC) + timedelta(hours=8)).isoformat()

    await scheduler.snooze(job_id, until_iso)

    rows = await tmp_db.fetch_all("SELECT next_run_at FROM jobs WHERE job_id = ?", (job_id,))
    assert rows[0]["next_run_at"] == until_iso
    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.snoozed' AND target_id = ?", (job_id,),
    )
    assert events == []
