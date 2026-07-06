"""Migration regression — the 3 legacy /agent use cases, corrected: goal_execution
IS a freeform cron goal (CronTrigger); morning_brief/check_in are NOT — they pin
their real handler via ReportTrigger (no goal text), proven by asserting the
ACTUAL handler_name projected, not just that SOME row exists."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.trigger import CronTrigger, ReportTrigger
from stackowl.scheduler.owl_lifecycle import _job_id_for, reconcile_owl_schedules

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


async def _owned_row(db: DbPool, name: str) -> dict:
    rows = await db.fetch_all("SELECT * FROM jobs WHERE job_id = ?", (_job_id_for(name),))
    assert rows, f"no projected row for {name}"
    row = dict(rows[0])
    row["params"] = json.loads(row["params"]) if isinstance(row["params"], str) else row["params"]
    return row


async def test_goal_execution_usecase_is_a_freeform_cron_goal(db: DbPool) -> None:
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name="newsowl", role="r", system_prompt="p", model_tier="fast",
            lifecycle="scheduled",
            trigger=CronTrigger(schedule="every 2h", prompt="poke me with the latest AI news"),
        ),
        source_name="t",
    )
    result = await reconcile_owl_schedules(reg, db)
    assert result.created == 1
    row = await _owned_row(db, "newsowl")
    assert row["handler_name"] == "goal_execution"
    assert row["params"]["goal"] == "poke me with the latest AI news"


@pytest.mark.parametrize(
    ("name", "report", "schedule"),
    [
        ("briefowl", "morning_brief", "daily@08:00"),
        ("checkowl", "check_in", "daily@18:00"),
    ],
)
async def test_report_usecase_projects_the_real_handler(
    db: DbPool, name: str, report: str, schedule: str
) -> None:
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name=name, role="r", system_prompt="p", model_tier="fast",
            lifecycle="scheduled",
            trigger=ReportTrigger(report=report, schedule=schedule),
        ),
        source_name="t",
    )
    result = await reconcile_owl_schedules(reg, db)
    assert result.created == 1
    row = await _owned_row(db, name)
    assert row["handler_name"] == report  # the REAL handler, not goal_execution
    assert row["next_run_at"] and row["status"] == "pending"
    # Idempotent — a second reconcile creates no duplicate.
    again = await reconcile_owl_schedules(reg, db)
    assert again.created == 0
