"""``JobScheduler.pause``/``.resume`` with a ``CommandContext`` (Story 4.3, AD-26).

I/O matrix row 4: ``JobScheduler.pause`` called twice with the SAME
``CommandContext.command_id`` (a simulated lease-reclaim re-run) is a no-op the
second time — the ``jobs`` row and the ``job.paused`` journal row are written
exactly once. Also proves ``write_audit`` still fires (with the requester's
actor) and that ``context=None`` (every pre-4.3 caller) is byte-identical to
before.
"""

from __future__ import annotations

import pytest

from stackowl.commands.spec.context import CommandContext
from stackowl.db.pool import DbPool
from stackowl.scheduler.scheduler import JobScheduler

pytestmark = pytest.mark.asyncio


async def _make_job(scheduler: JobScheduler) -> str:
    job = await scheduler.create_job(handler_name="goal_execution", schedule="daily@09:00")
    return job.job_id


async def test_pause_with_context_records_job_paused_exactly_once(
    tmp_db: DbPool,
) -> None:
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)
    context = CommandContext(
        command_id="cmd-pause-1", command_type="scheduling.pause_job",
        requester_kind="owner",
    )

    await scheduler.pause(job_id, context=context)

    rows = await tmp_db.fetch_all("SELECT status, enabled FROM jobs WHERE job_id = ?", (job_id,))
    assert rows[0]["status"] == "failed"
    assert rows[0]["enabled"] == 0

    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.paused' AND target_id = ?",
        (job_id,),
    )
    assert len(events) == 1


async def test_pause_re_run_with_the_same_command_id_is_a_no_op(
    tmp_db: DbPool,
) -> None:
    """The idempotency guard (command_receipts) — a lease-reclaim re-run must
    not write the jobs row or the journal row a second time."""
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)
    context = CommandContext(
        command_id="cmd-pause-reclaim", command_type="scheduling.pause_job",
        requester_kind="owner",
    )

    await scheduler.pause(job_id, context=context)
    # Resume it out-of-band so a SECOND pause with the SAME command_id would
    # be observable if it actually re-ran the mutation.
    await tmp_db.execute("UPDATE jobs SET status = 'pending', enabled = 1 WHERE job_id = ?", (job_id,))
    await scheduler.pause(job_id, context=context)

    # The re-run was a no-op: the out-of-band resume is still in effect.
    rows = await tmp_db.fetch_all("SELECT status, enabled FROM jobs WHERE job_id = ?", (job_id,))
    assert rows[0]["status"] == "pending"
    assert rows[0]["enabled"] == 1

    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.paused' AND target_id = ?",
        (job_id,),
    )
    assert len(events) == 1  # not two

    receipts = await tmp_db.fetch_all(
        "SELECT * FROM command_receipts WHERE command_id = ?", ("cmd-pause-reclaim",),
    )
    assert len(receipts) == 1


async def test_resume_with_context_records_job_resumed_exactly_once(
    tmp_db: DbPool,
) -> None:
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)
    await scheduler.pause(job_id)  # context=None — plain pre-4.3 pause first
    context = CommandContext(
        command_id="cmd-resume-1", command_type="scheduling.resume_job",
        requester_kind="owner",
    )

    await scheduler.resume(job_id, context=context)

    rows = await tmp_db.fetch_all("SELECT status, enabled FROM jobs WHERE job_id = ?", (job_id,))
    assert rows[0]["status"] == "pending"
    assert rows[0]["enabled"] == 1

    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.resumed' AND target_id = ?",
        (job_id,),
    )
    assert len(events) == 1


async def test_write_audit_still_fires_with_an_actor(tmp_db: DbPool) -> None:
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)
    context = CommandContext(
        command_id="cmd-pause-audit", command_type="scheduling.pause_job",
        requester_kind="owner",
    )

    await scheduler.pause(job_id, context=context)

    rows = await tmp_db.fetch_all(
        "SELECT actor, target FROM audit_log WHERE event_type = 'job_paused' AND target = ?",
        (job_id,),
    )
    assert len(rows) == 1
    assert rows[0]["actor"] == "owner"


async def test_context_none_keeps_the_pre_4_3_behavior(tmp_db: DbPool) -> None:
    """No context (every caller before this story) — no idempotency receipt,
    no job.paused journal row, same plain UPDATE + write_audit as always."""
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)

    await scheduler.pause(job_id)

    rows = await tmp_db.fetch_all("SELECT status, enabled FROM jobs WHERE job_id = ?", (job_id,))
    assert rows[0]["status"] == "failed"
    assert rows[0]["enabled"] == 0

    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.paused' AND target_id = ?",
        (job_id,),
    )
    assert events == []

    audit_rows = await tmp_db.fetch_all(
        "SELECT actor FROM audit_log WHERE event_type = 'job_paused' AND target = ?",
        (job_id,),
    )
    assert len(audit_rows) == 1
    assert audit_rows[0]["actor"] == "user"  # write_audit's own default
