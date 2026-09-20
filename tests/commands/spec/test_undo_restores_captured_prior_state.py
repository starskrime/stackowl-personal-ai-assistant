"""Story 4.7 -- undo restores the CAPTURED prior payload for a command whose
undo must reconstruct prior state (an edit, a snooze), not repeat the
forward payload; and supersession detection matches on ``job_id`` (not full
payload equality) for these -- two edits of the SAME job never share an
identical forward payload, so the pilot pair's own exact-string match could
never see a later edit as touching "the same target".
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import stackowl.scheduler.commands  # noqa: F401 -- registration side effect
from stackowl.commands.spec.submit import submit_command
from stackowl.commands.spec.undo import request_undo
from stackowl.db.pool import DbPool
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.scheduler.scheduler import JobScheduler
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def tmp_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "undo_restore.db"
    seed_schema(db_path)
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture(autouse=True)
def _bind_services(tmp_db: DbPool) -> Iterator[None]:
    """The real scheduler handlers (``scheduler/commands.py``) resolve their
    db via ``get_services()`` (``CommandHandler``'s shape is fixed at
    ``(payload, context) -> CommandOutcome`` -- no bespoke db parameter) --
    bind it for every test in this file, the same as any other caller of a
    real (non-stub) command handler."""
    token = set_services(StepServices(db_pool=tmp_db))
    yield
    reset_services(token)


async def _make_job(db: DbPool) -> str:
    scheduler = JobScheduler(db=db)
    job = await scheduler.create_job(
        handler_name="goal_execution", schedule="daily@09:00", params={"goal": "old goal"},
    )
    return job.job_id


# --------------------------------------------------------------------------- edit


async def test_undo_an_edit_restores_the_captured_prior_schedule_and_goal(
    tmp_db: DbPool,
) -> None:
    job_id = await _make_job(tmp_db)

    edit = await submit_command(
        tmp_db, "scheduling.edit_job",
        {"job_id": job_id, "schedule": "daily@10:00", "goal": "new goal"},
    )
    assert edit.outcome is not None and edit.outcome.success

    rows = await tmp_db.fetch_all("SELECT schedule, params FROM jobs WHERE job_id = ?", (job_id,))
    assert rows[0]["schedule"] == "daily@10:00"
    assert json.loads(rows[0]["params"])["goal"] == "new goal"

    outcome = await request_undo(tmp_db, edit.command_id)

    assert outcome.refusal is None
    assert outcome.submission is not None
    assert outcome.submission.outcome is not None
    assert outcome.submission.outcome.success is True

    rows = await tmp_db.fetch_all("SELECT schedule, params FROM jobs WHERE job_id = ?", (job_id,))
    assert rows[0]["schedule"] == "daily@09:00"
    assert json.loads(rows[0]["params"])["goal"] == "old goal"


async def test_a_later_edit_supersedes_an_earlier_edits_undo(tmp_db: DbPool) -> None:
    job_id = await _make_job(tmp_db)

    first_edit = await submit_command(
        tmp_db, "scheduling.edit_job",
        {"job_id": job_id, "schedule": "daily@10:00", "goal": None},
    )
    assert first_edit.outcome is not None and first_edit.outcome.success
    second_edit = await submit_command(
        tmp_db, "scheduling.edit_job",
        {"job_id": job_id, "schedule": "daily@11:00", "goal": None},
    )
    assert second_edit.outcome is not None and second_edit.outcome.success

    outcome = await request_undo(tmp_db, first_edit.command_id)

    assert outcome.submission is None
    assert outcome.refusal is not None
    assert outcome.refusal.code == "superseded"


async def test_undoing_the_latest_edit_is_still_allowed(tmp_db: DbPool) -> None:
    """Only an EARLIER edit is superseded -- the LATEST edit's own undo is
    still live (no command AFTER it touched the same job_id)."""
    job_id = await _make_job(tmp_db)

    first_edit = await submit_command(
        tmp_db, "scheduling.edit_job",
        {"job_id": job_id, "schedule": "daily@10:00", "goal": None},
    )
    assert first_edit.outcome is not None
    second_edit = await submit_command(
        tmp_db, "scheduling.edit_job",
        {"job_id": job_id, "schedule": "daily@11:00", "goal": None},
    )
    assert second_edit.outcome is not None

    outcome = await request_undo(tmp_db, second_edit.command_id)

    assert outcome.refusal is None
    assert outcome.submission is not None
    assert outcome.submission.outcome is not None
    assert outcome.submission.outcome.success is True


# --------------------------------------------------------------------------- snooze


async def test_undo_a_snooze_restores_the_captured_prior_next_run_at(tmp_db: DbPool) -> None:
    job_id = await _make_job(tmp_db)
    before = await tmp_db.fetch_all("SELECT next_run_at FROM jobs WHERE job_id = ?", (job_id,))
    prior_next_run_at = before[0]["next_run_at"]
    until_iso = (datetime.now(UTC) + timedelta(hours=8)).isoformat()

    snooze = await submit_command(
        tmp_db, "scheduling.set_owl_schedule", {"job_id": job_id, "until": until_iso},
    )
    assert snooze.outcome is not None and snooze.outcome.success

    outcome = await request_undo(tmp_db, snooze.command_id)

    assert outcome.refusal is None
    assert outcome.submission is not None
    assert outcome.submission.outcome is not None
    assert outcome.submission.outcome.success is True

    rows = await tmp_db.fetch_all("SELECT next_run_at FROM jobs WHERE job_id = ?", (job_id,))
    assert rows[0]["next_run_at"] == prior_next_run_at


async def test_a_later_snooze_supersedes_an_earlier_snoozes_undo(tmp_db: DbPool) -> None:
    job_id = await _make_job(tmp_db)
    until_1 = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    until_2 = (datetime.now(UTC) + timedelta(hours=2)).isoformat()

    first_snooze = await submit_command(
        tmp_db, "scheduling.set_owl_schedule", {"job_id": job_id, "until": until_1},
    )
    assert first_snooze.outcome is not None and first_snooze.outcome.success
    second_snooze = await submit_command(
        tmp_db, "scheduling.set_owl_schedule", {"job_id": job_id, "until": until_2},
    )
    assert second_snooze.outcome is not None and second_snooze.outcome.success

    outcome = await request_undo(tmp_db, first_snooze.command_id)

    assert outcome.submission is None
    assert outcome.refusal is not None
    assert outcome.refusal.code == "superseded"
