"""Task 3 — owl_build pause/resume reuse the scheduler primitives on the owl's
owned job row, and refuse cleanly for an owl with no schedule."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.trigger import CronTrigger
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.scheduler.owl_lifecycle import _job_id_for, reconcile_owl_schedules
from stackowl.tools.meta.owl_build import OwlBuildTool

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "sched.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _scheduled_owl(name: str) -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name, role="watcher", system_prompt="p", model_tier="fast",
        lifecycle="scheduled", trigger=CronTrigger(schedule="every 10m", prompt="do it"),
    )


async def _job_row(db: DbPool, job_id: str) -> dict:
    rows = await db.fetch_all("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    assert rows, f"expected a job row for {job_id}"
    return rows[0]


async def test_pause_then_resume_toggles_the_owned_row(db: DbPool) -> None:
    reg = OwlRegistry()
    reg.register(_scheduled_owl("scout"), source_name="t")
    await reconcile_owl_schedules(reg, db)  # project the owned job row
    token = set_services(StepServices(owl_registry=reg, db_pool=db))
    try:
        paused = await OwlBuildTool().execute(action="pause", name="scout")
        assert paused.success, paused.error
        row = await _job_row(db, _job_id_for("scout"))
        assert int(row["enabled"]) == 0 and row["status"] == "failed"

        resumed = await OwlBuildTool().execute(action="resume", name="scout")
        assert resumed.success, resumed.error
        row = await _job_row(db, _job_id_for("scout"))
        assert int(row["enabled"]) == 1 and row["status"] == "pending"
    finally:
        reset_services(token)


async def test_pause_refuses_on_demand_owl(db: DbPool) -> None:
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(name="resty", role="r", system_prompt="p", model_tier="fast"),
        source_name="t",
    )
    token = set_services(StepServices(owl_registry=reg, db_pool=db))
    try:
        result = await OwlBuildTool().execute(action="pause", name="resty")
        assert not result.success
        assert "no schedule to pause" in result.error
    finally:
        reset_services(token)
