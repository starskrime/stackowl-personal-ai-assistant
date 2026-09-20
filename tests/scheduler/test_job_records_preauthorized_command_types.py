"""Story 4.6 (FR33) -- a job created with declared
``preauthorized_command_types`` persists them with scope (the job's own
``job_id`` IS the scope -- ``standing_authority.scope_kind="job"``,
``scope_id=job.job_id``).

Modelled on ``tests/scheduler/test_pause_resume_are_commands.py``'s own
``tmp_db``-fixture style.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.scheduler import JobScheduler

pytestmark = pytest.mark.asyncio


async def test_create_job_persists_declared_command_types(tmp_db: DbPool) -> None:
    scheduler = JobScheduler(db=tmp_db)

    job = await scheduler.create_job(
        handler_name="goal_execution", schedule="daily@09:00",
        preauthorized_command_types=["delivery.send_message"],
    )

    assert job.preauthorized_command_types == ["delivery.send_message"]

    rows = await tmp_db.fetch_all(
        "SELECT preauthorized_command_types FROM jobs WHERE job_id = ?", (job.job_id,),
    )
    assert rows[0]["preauthorized_command_types"] == '["delivery.send_message"]'


async def test_create_job_without_declared_command_types_defaults_empty(
    tmp_db: DbPool,
) -> None:
    scheduler = JobScheduler(db=tmp_db)

    job = await scheduler.create_job(handler_name="goal_execution", schedule="daily@09:00")

    assert job.preauthorized_command_types == []
    rows = await tmp_db.fetch_all(
        "SELECT preauthorized_command_types FROM jobs WHERE job_id = ?", (job.job_id,),
    )
    assert rows[0]["preauthorized_command_types"] is None


async def test_list_jobs_round_trips_declared_command_types(tmp_db: DbPool) -> None:
    scheduler = JobScheduler(db=tmp_db)
    created = await scheduler.create_job(
        handler_name="goal_execution", schedule="daily@09:00",
        preauthorized_command_types=["delivery.send_message", "cronjob.delete"],
    )

    jobs = await scheduler.list_jobs()

    found = next(j for j in jobs if j.job_id == created.job_id)
    assert found.preauthorized_command_types == ["delivery.send_message", "cronjob.delete"]
