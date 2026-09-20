"""``JobScheduler.create_job``/``update_job``/``stop_job``/``run_now`` with a
``CommandContext`` (Story 4.7, AD-26).

I/O matrix rows this file proves, for each of the four extended mutators:
* a context-given call records the matching ``job.*`` journal event exactly
  once and (``context=None``) preserves every pre-4.7 caller's exact
  behavior byte-for-byte;
* a lease-reclaim re-run (the SAME ``command_id`` called twice) is a no-op —
  never re-applies the mutation, never re-records the domain event;
* ``create_job`` specifically: the second call returns the SAME job (read
  back via the receipt's captured ``job_id``), never minting a second row.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackowl.commands.spec.context import CommandContext
from stackowl.db.pool import DbPool
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.scheduler.scheduler import JobScheduler
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def tmp_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "create_edit_delete_run_now.db"
    seed_schema(db_path)
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture(autouse=True)
def _reset_registry() -> object:
    HandlerRegistry.reset()
    yield None
    HandlerRegistry.reset()


class _CountingHandler(JobHandler):
    def __init__(self) -> None:
        self.runs: list[str] = []

    @property
    def handler_name(self) -> str:
        return "goal_execution"

    async def execute(self, job: Job) -> JobResult:
        self.runs.append(job.job_id)
        return JobResult(job_id=job.job_id, success=True, output="ok", error=None, duration_ms=1.0)


async def _make_job(scheduler: JobScheduler) -> str:
    job = await scheduler.create_job(handler_name="goal_execution", schedule="daily@09:00")
    return job.job_id


# --------------------------------------------------------------------------- create_job


async def test_create_job_with_context_records_job_created_exactly_once(tmp_db: DbPool) -> None:
    scheduler = JobScheduler(db=tmp_db)
    context = CommandContext(
        command_id="cmd-create-1", command_type="scheduling.create_job",
        requester_kind="owner",
    )

    job = await scheduler.create_job(
        handler_name="goal_execution", schedule="daily@09:00", context=context,
    )

    rows = await tmp_db.fetch_all("SELECT job_id FROM jobs WHERE job_id = ?", (job.job_id,))
    assert len(rows) == 1

    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.created' AND target_id = ?",
        (job.job_id,),
    )
    assert len(events) == 1


async def test_create_job_re_run_with_the_same_command_id_returns_the_same_job(
    tmp_db: DbPool,
) -> None:
    scheduler = JobScheduler(db=tmp_db)
    context = CommandContext(
        command_id="cmd-create-reclaim", command_type="scheduling.create_job",
        requester_kind="owner",
    )

    first = await scheduler.create_job(
        handler_name="goal_execution", schedule="daily@09:00", context=context,
    )
    second = await scheduler.create_job(
        handler_name="goal_execution", schedule="daily@09:00", context=context,
    )

    assert second.job_id == first.job_id
    rows = await tmp_db.fetch_all("SELECT job_id FROM jobs WHERE handler_name = 'goal_execution'")
    assert len(rows) == 1, "a lease-reclaim re-run must not mint a second row"

    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.created' AND target_id = ?",
        (first.job_id,),
    )
    assert len(events) == 1  # not two


async def test_create_job_concurrent_calls_with_the_same_command_id_mint_one_job(
    tmp_db: DbPool,
) -> None:
    """The TOCTOU race window between the outer pre-check
    (``_existing_created_job``) and the transaction's own receipt write,
    driven with a REAL concurrent race (``asyncio.gather``), not just the
    sequential re-run above — mirrors ``tests/scheduler/
    test_poller_cas_claim.py``'s own "the REAL race (concurrent gather), not
    just a sequential check" precedent. Exactly one ``jobs`` row and one
    ``job.created`` event must exist for this ``command_id``, and both
    concurrent calls must return the SAME ``job_id``."""
    import asyncio

    scheduler = JobScheduler(db=tmp_db)
    context = CommandContext(
        command_id="cmd-create-concurrent", command_type="scheduling.create_job",
        requester_kind="owner",
    )

    first, second = await asyncio.gather(
        scheduler.create_job(
            handler_name="goal_execution", schedule="daily@09:00", context=context,
        ),
        scheduler.create_job(
            handler_name="goal_execution", schedule="daily@09:00", context=context,
        ),
    )

    assert first.job_id == second.job_id

    rows = await tmp_db.fetch_all(
        "SELECT job_id FROM jobs WHERE handler_name = 'goal_execution'",
    )
    assert len(rows) == 1, "a genuine concurrent race must not mint two rows"

    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.created' AND target_id = ?",
        (first.job_id,),
    )
    assert len(events) == 1, "a genuine concurrent race must not double-journal"


async def test_create_job_context_none_keeps_the_pre_4_7_behavior(tmp_db: DbPool) -> None:
    scheduler = JobScheduler(db=tmp_db)

    job = await scheduler.create_job(handler_name="goal_execution", schedule="daily@09:00")

    rows = await tmp_db.fetch_all("SELECT job_id FROM jobs WHERE job_id = ?", (job.job_id,))
    assert len(rows) == 1
    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.created' AND target_id = ?",
        (job.job_id,),
    )
    assert events == []


# --------------------------------------------------------------------------- update_job (edit)


async def test_update_job_with_context_records_job_edited_exactly_once(tmp_db: DbPool) -> None:
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)
    context = CommandContext(
        command_id="cmd-edit-1", command_type="scheduling.edit_job",
        requester_kind="owner",
    )

    updated = await scheduler.update_job(job_id, schedule="daily@10:00", context=context)

    assert updated is not None
    rows = await tmp_db.fetch_all("SELECT schedule FROM jobs WHERE job_id = ?", (job_id,))
    assert rows[0]["schedule"] == "daily@10:00"

    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.edited' AND target_id = ?", (job_id,),
    )
    assert len(events) == 1


async def test_update_job_re_run_with_the_same_command_id_is_a_no_op(tmp_db: DbPool) -> None:
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)
    context = CommandContext(
        command_id="cmd-edit-reclaim", command_type="scheduling.edit_job",
        requester_kind="owner",
    )

    await scheduler.update_job(job_id, schedule="daily@10:00", context=context)
    # Mutate it out-of-band so a SECOND edit with the SAME command_id would be
    # observable if it actually re-ran the mutation.
    await tmp_db.execute("UPDATE jobs SET schedule = 'daily@23:00' WHERE job_id = ?", (job_id,))
    await scheduler.update_job(job_id, schedule="daily@10:00", context=context)

    rows = await tmp_db.fetch_all("SELECT schedule FROM jobs WHERE job_id = ?", (job_id,))
    assert rows[0]["schedule"] == "daily@23:00"  # the re-run did not touch it

    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.edited' AND target_id = ?", (job_id,),
    )
    assert len(events) == 1


async def test_update_job_captures_the_prior_schedule_and_goal_as_undo_payload(
    tmp_db: DbPool,
) -> None:
    scheduler = JobScheduler(db=tmp_db)
    job = await scheduler.create_job(
        handler_name="goal_execution", schedule="daily@09:00", params={"goal": "old goal"},
    )
    context = CommandContext(
        command_id="cmd-edit-undo", command_type="scheduling.edit_job",
        requester_kind="owner",
    )

    await scheduler.update_job(
        job.job_id, schedule="daily@10:00", goal="new goal", context=context,
    )

    rows = await tmp_db.fetch_all(
        "SELECT undo_payload FROM command_receipts WHERE command_id = ?", ("cmd-edit-undo",),
    )
    assert len(rows) == 1
    import json

    captured = json.loads(rows[0]["undo_payload"])
    assert captured == {"job_id": job.job_id, "schedule": "daily@09:00", "goal": "old goal"}


async def test_update_job_context_none_keeps_the_pre_4_7_behavior(tmp_db: DbPool) -> None:
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)

    updated = await scheduler.update_job(job_id, schedule="daily@10:00")

    assert updated is not None
    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.edited' AND target_id = ?", (job_id,),
    )
    assert events == []


# --------------------------------------------------------------------------- stop_job (delete)


async def test_stop_job_with_context_records_job_deleted_exactly_once(tmp_db: DbPool) -> None:
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)
    context = CommandContext(
        command_id="cmd-delete-1", command_type="scheduling.delete_job",
        requester_kind="owner",
    )

    await scheduler.stop_job(job_id, context=context)

    rows = await tmp_db.fetch_all("SELECT job_id FROM jobs WHERE job_id = ?", (job_id,))
    assert rows == []

    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.deleted' AND target_id = ?", (job_id,),
    )
    assert len(events) == 1


async def test_stop_job_re_run_with_the_same_command_id_is_a_no_op(tmp_db: DbPool) -> None:
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)
    context = CommandContext(
        command_id="cmd-delete-reclaim", command_type="scheduling.delete_job",
        requester_kind="owner",
    )

    await scheduler.stop_job(job_id, context=context)
    await scheduler.stop_job(job_id, context=context)  # must not raise / double-record

    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.deleted' AND target_id = ?", (job_id,),
    )
    assert len(events) == 1


async def test_stop_job_context_none_keeps_the_pre_4_7_behavior(tmp_db: DbPool) -> None:
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)

    await scheduler.stop_job(job_id)

    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.deleted' AND target_id = ?", (job_id,),
    )
    assert events == []


# --------------------------------------------------------------------------- run_now


async def test_run_now_with_context_records_job_run_now_triggered(tmp_db: DbPool) -> None:
    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)
    await tmp_db.execute("UPDATE jobs SET status = 'pending' WHERE job_id = ?", (job_id,))
    context = CommandContext(
        command_id="cmd-run-1", command_type="scheduling.run_now_job",
        requester_kind="owner",
    )

    result = await scheduler.run_now(job_id, context=context)

    assert result is not None and result.success is True
    assert handler.runs == [job_id]
    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.run_now_triggered' AND target_id = ?",
        (job_id,),
    )
    assert len(events) == 1
    # job.started is still recorded too (untouched, per Design Notes).
    started = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.started' AND target_id = ?", (job_id,),
    )
    assert len(started) == 1


async def test_run_now_re_run_with_the_same_command_id_does_not_retrigger_the_handler(
    tmp_db: DbPool,
) -> None:
    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)
    await tmp_db.execute("UPDATE jobs SET status = 'pending' WHERE job_id = ?", (job_id,))
    context = CommandContext(
        command_id="cmd-run-reclaim", command_type="scheduling.run_now_job",
        requester_kind="owner",
    )

    first = await scheduler.run_now(job_id, context=context)
    second = await scheduler.run_now(job_id, context=context)

    assert first is not None and first.success is True
    assert second is not None and second.success is True
    assert handler.runs == [job_id], "the re-run must NOT invoke the handler a second time"
    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.run_now_triggered' AND target_id = ?",
        (job_id,),
    )
    assert len(events) == 1


async def test_run_now_context_none_keeps_the_pre_4_7_behavior(tmp_db: DbPool) -> None:
    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    scheduler = JobScheduler(db=tmp_db)
    job_id = await _make_job(scheduler)
    await tmp_db.execute("UPDATE jobs SET status = 'pending' WHERE job_id = ?", (job_id,))

    result = await scheduler.run_now(job_id)

    assert result is not None and result.success is True
    events = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'job.run_now_triggered' AND target_id = ?",
        (job_id,),
    )
    assert events == []
