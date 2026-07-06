"""Migration regression — the 3 legacy /agent use cases (a recurring goal, a
morning brief, a check-in) expressed as scheduled owls all project exactly one
owned scheduler row that fires on schedule with the intended goal."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.owls.registry import OwlRegistry
from stackowl.scheduler.owl_lifecycle import _job_id_for, reconcile_owl_schedules
from stackowl.tools.meta.owl_build_authz import build_agent_manifest
from stackowl.tools.meta.owl_build_spec import OwlBuildSpec

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


def _scheduled(name: str, goal: str, schedule: str) -> OwlRegistry:
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifestFrom(name, goal, schedule), source_name="usecase"
    )
    return reg


def OwlAgentManifestFrom(name: str, goal: str, schedule: str):  # noqa: N802
    spec = OwlBuildSpec(
        action="create", name=name, preset="researcher",
        specialty=goal, schedule=schedule, goal=goal,
    )
    reg0 = OwlRegistry()  # unbounded secretary creator → SAFE_DEFAULT_CEILING
    from stackowl.owls.manifest import OwlAgentManifest
    reg0.register(
        OwlAgentManifest(name="secretary", role="s", system_prompt="p", model_tier="fast"),
        source_name="t",
    )
    manifest, _ = build_agent_manifest(
        spec, creator="secretary", parent_ceiling=None, registry=reg0
    )
    assert manifest.lifecycle == "scheduled"
    assert manifest.trigger is not None and manifest.trigger.prompt == goal
    return manifest


async def _owned_row(db: DbPool, name: str) -> dict:
    rows = await db.fetch_all("SELECT * FROM jobs WHERE job_id = ?", (_job_id_for(name),))
    assert rows, f"no projected row for {name}"
    row = dict(rows[0])
    row["params"] = json.loads(row["params"]) if isinstance(row["params"], str) else row["params"]
    return row


@pytest.mark.parametrize(
    ("name", "goal", "schedule"),
    [
        ("newsowl", "poke me with the latest AI news", "every 2h"),     # goal_execution
        ("briefowl", "give me my morning brief", "daily@09:00"),        # morning_brief
        ("checkowl", "check in on my open tasks", "daily@17:00"),       # check_in
    ],
)
async def test_agent_usecase_projects_one_firing_row(
    db: DbPool, name: str, goal: str, schedule: str
) -> None:
    reg = _scheduled(name, goal, schedule)
    result = await reconcile_owl_schedules(reg, db)
    assert result.created == 1
    row = await _owned_row(db, name)
    assert row["handler_name"] == "goal_execution"
    assert row["params"]["goal"] == goal
    assert row["next_run_at"] and row["status"] == "pending"
    # Idempotent — a second reconcile creates no duplicate (same end behaviour).
    again = await reconcile_owl_schedules(reg, db)
    assert again.created == 0
