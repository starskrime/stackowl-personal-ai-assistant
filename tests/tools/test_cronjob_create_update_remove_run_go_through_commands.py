"""``cronjob`` tool's ``create``/``update``/``run`` actions run as COMMAND
tasks (Story 4.7) -- ``remove`` is covered in
``tests/tools/test_cronjob_pause_resume_go_through_commands.py`` (extended
there, alongside its Story 4.3 pause/resume siblings).

``create``/``update`` (``scheduling.create_job``/``edit_job``) are
REVERSIBLE, so an owner's own call runs AT ONCE, exactly like pause/resume.
``run`` (``scheduling.run_now_job``) is declared IRREVERSIBLE (Design
Notes), so an ordinary owner call parks awaiting step-up -- the job is NOT
actually run by this call, and the tool reports that honestly.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

# Registration side effect (mirrors journal/task_events.py's own shape).
import stackowl.scheduler.commands  # noqa: F401
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.tools.base import ToolResult
from stackowl.tools.scheduling.cronjob import CronjobTool
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio

_SESSION = "sess-cron-create-update-run-1"
_OWL = "scout"


@pytest.fixture()
async def migrated_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "cron_create_update_run.db"
    seed_schema(db_path)
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def _seed_session(db: DbPool, session_key: str = _SESSION, owl: str = _OWL) -> None:
    await db.execute(
        "INSERT INTO conversations (id, session_key, owl_name, started_at, message_count) "
        "VALUES (?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, session_key, owl, datetime.now(UTC).isoformat(), 0),
    )


async def _run(db: DbPool, **kwargs: object) -> ToolResult:
    token = set_services(StepServices(db_pool=db))
    ttoken = TraceContext.start(session_key=_SESSION, interactive=True, channel="cli")
    try:
        return await CronjobTool().execute(**kwargs)
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)


def _payload(result: ToolResult) -> dict[str, Any]:
    return json.loads(result.output)


async def _create_job(db: DbPool) -> str:
    await _seed_session(db)
    created = _payload(
        await _run(db, action="create", prompt="do the thing", schedule="every 2h")
    )
    assert created["created"] is True
    return created["job_id"]


# --------------------------------------------------------------------------- create


async def test_create_submits_a_command_task_and_actually_creates(migrated_db: DbPool) -> None:
    await _seed_session(migrated_db)

    result = await _run(migrated_db, action="create", prompt="do the thing", schedule="every 2h")

    assert result.success
    payload = _payload(result)
    assert payload["created"] is True
    job_id = payload["job_id"]

    jobs = await JobScheduler(db=migrated_db).list_jobs()
    assert any(j.job_id == job_id for j in jobs)

    rows = await migrated_db.fetch_all(
        "SELECT command_type, status FROM tasks WHERE kind = 'command' "
        "AND command_type = 'scheduling.create_job'"
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"


# --------------------------------------------------------------------------- update


async def test_update_submits_a_command_task_and_actually_updates(migrated_db: DbPool) -> None:
    job_id = await _create_job(migrated_db)

    result = await _run(
        migrated_db, action="update", job_id=job_id, schedule="every 3h", prompt="do the new thing",
    )

    assert result.success
    payload = _payload(result)
    assert payload["updated"] is True
    assert payload["schedule"] == "every 3h"

    jobs = await JobScheduler(db=migrated_db).list_jobs()
    updated_job = next(j for j in jobs if j.job_id == job_id)
    assert updated_job.schedule == "every 3h"
    assert updated_job.params.get("goal") == "do the new thing"

    rows = await migrated_db.fetch_all(
        "SELECT command_type, status FROM tasks WHERE kind = 'command' "
        "AND command_type = 'scheduling.edit_job'"
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"


async def test_update_on_an_unowned_job_is_refused_before_submitting_a_command(
    migrated_db: DbPool,
) -> None:
    await _seed_session(migrated_db)
    foreign = await JobScheduler(db=migrated_db).create_job(
        handler_name="goal_execution", schedule="daily@09:00",
        params={"goal": "not mine", "created_by": "cronjob", "owl": "someone_else"},
    )

    result = await _run(migrated_db, action="update", job_id=foreign.job_id, schedule="every 1h")

    assert not result.success
    rows = await migrated_db.fetch_all(
        "SELECT 1 FROM tasks WHERE kind = 'command' AND command_type = 'scheduling.edit_job'"
    )
    assert rows == []


# --------------------------------------------------------------------------- run


async def test_run_submits_a_command_task_and_needs_step_up(migrated_db: DbPool) -> None:
    """scheduling.run_now_job is declared IRREVERSIBLE (Design Notes), so an
    ordinary owner call parks awaiting step-up -- the job is NOT actually
    run by this call."""
    job_id = await _create_job(migrated_db)

    result = await _run(migrated_db, action="run", job_id=job_id)

    assert result.success
    payload = _payload(result)
    assert payload["ran"] is False
    assert payload["pending_approval"] is True
    assert payload["job_id"] == job_id
    assert result.side_effect_committed is False

    # Still pending — nothing dispatched.
    jobs = await JobScheduler(db=migrated_db).list_jobs()
    job = next(j for j in jobs if j.job_id == job_id)
    assert job.status == "pending"

    rows = await migrated_db.fetch_all(
        "SELECT command_type, status FROM tasks WHERE kind = 'command' "
        "AND command_type = 'scheduling.run_now_job'"
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "parked"


async def test_run_on_an_unowned_job_is_refused_before_submitting_a_command(
    migrated_db: DbPool,
) -> None:
    await _seed_session(migrated_db)
    foreign = await JobScheduler(db=migrated_db).create_job(
        handler_name="goal_execution", schedule="daily@09:00",
        params={"goal": "not mine", "created_by": "cronjob", "owl": "someone_else"},
    )

    result = await _run(migrated_db, action="run", job_id=foreign.job_id)

    assert not result.success
    rows = await migrated_db.fetch_all(
        "SELECT 1 FROM tasks WHERE kind = 'command' AND command_type = 'scheduling.run_now_job'"
    )
    assert rows == []
