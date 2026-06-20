"""WS-A — JobScheduler.create_job persists durable delivery-target columns.

``create_job`` gained two keyword params (``target_channels`` /
``target_addresses``) so a producer that seeds a proactive job stamps the
recipient onto the durable job row at creation time. ``insert_job`` already
serializes them and ``row_to_job`` round-trips them; these tests pin that the
columns survive a create → reload cycle, and that a call WITHOUT the new params
is byte-identical (empty columns) to the prior behavior.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.scheduler import JobScheduler

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def migrated_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "sched.db"
    MigrationRunner(db_path=db_path).run()
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


async def test_create_job_persists_durable_targets(migrated_db: DbPool) -> None:
    sched = JobScheduler(db=migrated_db)
    job = await sched.create_job(
        handler_name="goal_execution",
        schedule="every 30m",
        target_channels=["telegram"],
        target_addresses={"telegram": 12345},
    )
    # In-memory shape is correct.
    assert job.target_channels == ["telegram"]
    assert job.target_addresses == {"telegram": 12345}

    # And it round-trips through the durable row.
    reloaded = next(j for j in await sched.list_jobs() if j.job_id == job.job_id)
    assert reloaded.target_channels == ["telegram"]
    assert reloaded.target_addresses == {"telegram": 12345}


async def test_create_job_without_targets_is_byte_identical(migrated_db: DbPool) -> None:
    sched = JobScheduler(db=migrated_db)
    job = await sched.create_job(
        handler_name="goal_execution",
        schedule="every 30m",
    )
    assert job.target_channels == []
    assert job.target_addresses == {}

    reloaded = next(j for j in await sched.list_jobs() if j.job_id == job.job_id)
    assert reloaded.target_channels == []
    assert reloaded.target_addresses == {}
