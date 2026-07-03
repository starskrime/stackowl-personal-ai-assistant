"""End-to-end test for cronjob create with 'at HH:MM' schedule token."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.tools.base import ToolResult
from stackowl.tools.scheduling.cronjob import CronjobTool

pytestmark = pytest.mark.asyncio

_SESSION = "sess-cron-at-1"
_OWL = "scout"


@pytest.fixture()
async def migrated_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "cron.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def _seed_session(db: DbPool, session_id: str = _SESSION, owl: str = _OWL) -> None:
    await db.execute(
        "INSERT INTO conversations (id, session_id, owl_name, started_at, message_count) "
        "VALUES (?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, session_id, owl, datetime.now(UTC).isoformat(), 0),
    )


async def _run(
    db: DbPool, *, interactive: bool = True, session_id: str = _SESSION, **kwargs: object
) -> ToolResult:
    token = set_services(StepServices(db_pool=db))
    ttoken = TraceContext.start(
        session_id=session_id, interactive=interactive, channel="cli"
    )
    try:
        return await CronjobTool().execute(**kwargs)
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)


def _payload(result: ToolResult) -> dict[str, Any]:
    return json.loads(result.output)


@pytest.mark.asyncio
async def test_cronjob_create_at_schedule_is_one_shot(migrated_db: DbPool) -> None:
    """A 'remind me at 5pm today' schedule must persist run_once=True, never
    a recurring daily cadence."""
    await _seed_session(migrated_db)
    result = await _run(
        migrated_db, action="create", prompt="watch the movie", schedule="at 17:00"
    )
    assert result.success
    jobs = await JobScheduler(db=migrated_db).list_jobs()
    job = next(j for j in jobs if j.params.get("goal") == "watch the movie")
    assert job.params.get("run_once") is True
    assert job.schedule == "at 17:00"
