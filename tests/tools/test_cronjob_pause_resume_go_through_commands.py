"""``cronjob`` tool's ``pause``/``resume`` actions run as COMMAND tasks (Story 4.3).

The pilot migration this story ships: ``pause``/``resume`` now call
``commands/spec/submit.py::submit_command`` (never ``JobScheduler.pause``/
``.resume`` directly — proven independently by
``tests/commands/spec/test_one_door_tripwires.py``'s AST scan). This file
proves the OBSERVABLE behavior: a real ``kind='command'`` task row lands, the
job's status actually changes, and the tool's own ``ToolResult`` shape is
unchanged from before this story.

``remove`` is now ALSO migrated (Story 4.7, ``scheduling.delete_job`` —
IRREVERSIBLE, so an ordinary owner call parks awaiting step-up rather than
completing instantly); see
``tests/tools/test_cronjob_create_update_remove_run_go_through_commands.py``
for its full coverage. The one test here that used to assert "remove stays
unmigrated" is kept, corrected to the new reality, so this file's own
narrative never drifts from what the tool actually does.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

# Registration side effect (mirrors journal/task_events.py's own shape) — the
# two pilot CommandSpecs/handlers. Production gets this for free from
# startup/orchestrator.py's `_phase_gateway` boot import; a test that builds
# CronjobTool directly, with no orchestrator boot, needs it explicitly.
import stackowl.scheduler.commands  # noqa: F401
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.tools.base import ToolResult
from stackowl.tools.scheduling.cronjob import CronjobTool
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio

_SESSION = "sess-cron-commands-1"
_OWL = "scout"


@pytest.fixture()
async def migrated_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "cron_commands.db"
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


async def test_pause_submits_a_command_task_and_actually_pauses(
    migrated_db: DbPool,
) -> None:
    job_id = await _create_job(migrated_db)

    result = await _run(migrated_db, action="pause", job_id=job_id)

    assert result.success
    assert _payload(result) == {"pause": True, "job_id": job_id}

    jobs = await JobScheduler(db=migrated_db).list_jobs()
    paused = next(j for j in jobs if j.job_id == job_id)
    assert paused.enabled is False

    rows = await migrated_db.fetch_all(
        "SELECT command_type, status FROM tasks WHERE kind = 'command' "
        "AND command_type = 'scheduling.pause_job'"
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"


async def test_resume_submits_a_command_task_and_actually_resumes(
    migrated_db: DbPool,
) -> None:
    job_id = await _create_job(migrated_db)
    await _run(migrated_db, action="pause", job_id=job_id)

    result = await _run(migrated_db, action="resume", job_id=job_id)

    assert result.success
    assert _payload(result) == {"resume": True, "job_id": job_id}

    jobs = await JobScheduler(db=migrated_db).list_jobs()
    resumed = next(j for j in jobs if j.job_id == job_id)
    assert resumed.enabled is True

    rows = await migrated_db.fetch_all(
        "SELECT command_type, status FROM tasks WHERE kind = 'command' "
        "AND command_type = 'scheduling.resume_job'"
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"


async def test_remove_now_submits_a_command_task_and_needs_step_up(
    migrated_db: DbPool,
) -> None:
    """Story 4.7 — ``remove`` (``scheduling.delete_job``) is declared
    IRREVERSIBLE, so the gate demands step-up for EVERY requester kind,
    including the owner (Design Notes). The job is NOT removed by this
    call — the command is parked awaiting the owner's approval, and the
    tool reports that honestly rather than claiming success."""
    job_id = await _create_job(migrated_db)

    result = await _run(migrated_db, action="remove", job_id=job_id)

    assert result.success
    payload = _payload(result)
    assert payload["remove"] is False
    assert payload["pending_approval"] is True
    assert payload["job_id"] == job_id
    assert result.side_effect_committed is False

    # Nothing mutated yet — the row is still there.
    remaining = {j.job_id for j in await JobScheduler(db=migrated_db).list_jobs()}
    assert job_id in remaining

    rows = await migrated_db.fetch_all(
        "SELECT command_type, status FROM tasks WHERE kind = 'command' "
        "AND command_type = 'scheduling.delete_job'"
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "parked"


async def test_pause_on_an_unowned_job_is_refused_before_submitting_a_command(
    migrated_db: DbPool,
) -> None:
    """Ownership gating still runs FIRST — a foreign job never reaches
    submit_command at all (no orphan command task)."""
    await _seed_session(migrated_db)
    foreign = await JobScheduler(db=migrated_db).create_job(
        handler_name="goal_execution", schedule="daily@09:00",
        params={"goal": "not mine", "created_by": "cronjob", "owl": "someone_else"},
    )

    result = await _run(migrated_db, action="pause", job_id=foreign.job_id)

    assert not result.success
    rows = await migrated_db.fetch_all("SELECT 1 FROM tasks WHERE kind = 'command'")
    assert rows == []
