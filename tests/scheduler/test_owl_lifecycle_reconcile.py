"""ADR-B / S9+S11 — owl-lifecycle → scheduler projection (reconcile loop).

Covers the drift scenarios that are the whole point of the projection contract:
scheduled owl → exactly one owned row; idempotent re-run (no duplicate); on_demand
→ no row; retired owl → owned row deleted; a hand-made cronjob is NEVER touched;
edit schedule → row updated in place (not duplicated); quota cap; interval floor.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.owl_schedule_guards import (
    OWL_LIFECYCLE_SOURCE,
    interval_floor_error,
)
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.trigger import CronTrigger, ReportTrigger, ThresholdTrigger, WatchTrigger
from stackowl.scheduler.owl_lifecycle import _job_id_for, reconcile_owl_schedules
from stackowl.scheduler.scheduler import JobScheduler

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


def _scheduled_owl(name: str, *, schedule: str = "every 10m", prompt: str = "do it") -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name,
        role="watcher",
        system_prompt="watch things",
        model_tier="fast",
        lifecycle="scheduled",
        trigger=CronTrigger(schedule=schedule, prompt=prompt),
    )


async def _owned_rows(db: DbPool) -> list[dict]:
    rows = await db.fetch_all("SELECT * FROM jobs")
    out = []
    for r in rows:
        import json

        params = json.loads(r["params"]) if isinstance(r["params"], str) else (r["params"] or {})
        if params.get("source") == OWL_LIFECYCLE_SOURCE:
            out.append({**r, "params": params})
    return out


async def test_scheduled_owl_creates_exactly_one_owned_row(db: DbPool) -> None:
    reg = OwlRegistry()
    reg.register(_scheduled_owl("watcher"))
    result = await reconcile_owl_schedules(reg, db)
    rows = await _owned_rows(db)
    assert result.created == 1
    assert len(rows) == 1
    assert rows[0]["handler_name"] == "goal_execution"
    assert rows[0]["params"]["owner"] == "watcher"
    assert rows[0]["params"]["goal"] == "do it"


async def test_reconcile_is_idempotent(db: DbPool) -> None:
    reg = OwlRegistry()
    reg.register(_scheduled_owl("watcher"))
    await reconcile_owl_schedules(reg, db)
    second = await reconcile_owl_schedules(reg, db)
    rows = await _owned_rows(db)
    assert len(rows) == 1  # NO duplicate
    assert second.created == 0 and second.updated == 0 and second.deleted == 0


async def test_on_demand_owl_creates_no_row(db: DbPool) -> None:
    reg = OwlRegistry.with_default_secretary()  # secretary is on_demand
    result = await reconcile_owl_schedules(reg, db)
    assert await _owned_rows(db) == []
    assert result.created == 0


async def test_retired_scheduled_owl_deletes_owned_row(db: DbPool) -> None:
    reg = OwlRegistry()
    reg.register(_scheduled_owl("watcher"))
    await reconcile_owl_schedules(reg, db)
    assert len(await _owned_rows(db)) == 1
    # Retire: the owl leaves the registry; reconcile must tear its row down.
    reg.deregister("watcher")
    result = await reconcile_owl_schedules(reg, db)
    assert result.deleted == 1
    assert await _owned_rows(db) == []


async def test_retired_owl_with_run_history_still_deletes_owned_row(db: DbPool) -> None:
    """A job that has ACTUALLY RUN (has job_runs history) must still delete cleanly.

    Root cause (0080): job_runs.job_id had no ON DELETE CASCADE, so retiring an
    owl whose job had ever executed raised `FOREIGN KEY constraint failed` on
    the DELETE — caught by reconcile's per-row B5 guard, so the row silently
    stayed behind (drift) instead of the delete actually happening.
    """
    reg = OwlRegistry()
    reg.register(_scheduled_owl("watcher"))
    await reconcile_owl_schedules(reg, db)
    rows = await _owned_rows(db)
    job_id = rows[0]["job_id"]
    # Simulate the job having actually executed at least once.
    await db.execute(
        "INSERT INTO job_runs (run_id, job_id, idempotency_key, ran_at) "
        "VALUES (?, ?, ?, ?)",
        ("run-1", job_id, "idem-1", "2026-01-01T00:00:00"),
    )

    reg.deregister("watcher")
    result = await reconcile_owl_schedules(reg, db)

    assert result.deleted == 1
    assert await _owned_rows(db) == []


async def test_handmade_cronjob_is_never_touched(db: DbPool) -> None:
    # A user's own cronjob-tool row (created_by='cronjob', no source marker).
    sched = JobScheduler(db=db)
    handmade = await sched.create_job(
        handler_name="goal_execution",
        schedule="every 30m",
        params={"created_by": "cronjob", "owl": "secretary", "goal": "user job"},
    )
    reg = OwlRegistry()  # no scheduled owls at all
    result = await reconcile_owl_schedules(reg, db)
    assert result.deleted == 0
    still = [j for j in await sched.list_jobs() if j.job_id == handmade.job_id]
    assert len(still) == 1  # untouched


async def test_edit_schedule_updates_row_not_duplicated(db: DbPool) -> None:
    reg = OwlRegistry()
    reg.register(_scheduled_owl("watcher", schedule="every 10m"))
    await reconcile_owl_schedules(reg, db)
    before = await _owned_rows(db)
    assert before[0]["schedule"] == "every 10m"
    job_id = before[0]["job_id"]
    # Edit the owl's cron schedule (manifest = truth) and reconcile.
    reg.replace(_scheduled_owl("watcher", schedule="every 30m"))
    result = await reconcile_owl_schedules(reg, db)
    after = await _owned_rows(db)
    assert result.updated == 1
    assert len(after) == 1  # SAME row, not a duplicate
    assert after[0]["job_id"] == job_id
    assert after[0]["schedule"] == "every 30m"


async def test_watch_trigger_projects_website_watch(db: DbPool) -> None:
    reg = OwlRegistry()
    owl = OwlAgentManifest(
        name="pricewatch", role="watcher", system_prompt="x", model_tier="fast",
        lifecycle="scheduled",
        trigger=WatchTrigger(target="https://example.com", schedule="every 15m"),
    )
    reg.register(owl)
    await reconcile_owl_schedules(reg, db)
    rows = await _owned_rows(db)
    assert len(rows) == 1
    assert rows[0]["handler_name"] == "website_watch"
    assert rows[0]["params"]["url"] == "https://example.com"


async def test_threshold_trigger_projects_threshold_watch(db: DbPool) -> None:
    reg = OwlRegistry()
    owl = OwlAgentManifest(
        name="alerter", role="watcher", system_prompt="x", model_tier="fast",
        lifecycle="scheduled",
        trigger=ThresholdTrigger(
            source="https://example.com/reading", op="gt", threshold=70000.0,
            schedule="every 5m", prompt="ping me",
        ),
    )
    reg.register(owl)
    result = await reconcile_owl_schedules(reg, db)
    rows = await _owned_rows(db)
    assert result.created == 1
    assert len(rows) == 1
    row = rows[0]
    assert row["handler_name"] == "threshold_watch"
    assert row["params"]["watch_source"] == "https://example.com/reading"
    assert row["params"]["op"] == "gt"
    assert row["params"]["threshold"] == 70000.0
    assert row["params"]["prompt"] == "ping me"
    assert row["params"]["owner"] == "alerter"
    # Idempotent: a second pass makes no change (no duplicate row).
    second = await reconcile_owl_schedules(reg, db)
    assert second.created == 0 and second.updated == 0 and second.deleted == 0
    assert len(await _owned_rows(db)) == 1


async def test_quota_caps_scheduled_projection_at_five(db: DbPool) -> None:
    reg = OwlRegistry()
    for i in range(6):
        reg.register(_scheduled_owl(f"watcher{i}"))
    result = await reconcile_owl_schedules(reg, db)
    rows = await _owned_rows(db)
    assert len(rows) == 5  # cap enforced defensively at the projection
    assert result.created == 5
    assert result.skipped == 1


async def test_report_trigger_projects_the_named_handler(db: DbPool) -> None:
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name="briefowl", role="r", system_prompt="p", model_tier="fast",
            lifecycle="scheduled",
            trigger=ReportTrigger(report="morning_brief", schedule="daily@08:00"),
        ),
    )
    result = await reconcile_owl_schedules(reg, db)
    assert result.created == 1
    rows = await db.fetch_all("SELECT * FROM jobs WHERE job_id = ?", (_job_id_for("briefowl"),))
    assert rows[0]["handler_name"] == "morning_brief"  # NOT goal_execution


async def test_interval_floor_rejects_sub_five_minutes() -> None:
    # Guard helper.
    assert interval_floor_error("every 1m") is not None
    assert interval_floor_error("every 30s") is not None
    assert interval_floor_error("every 5m") is None
    assert interval_floor_error("every 10m") is None
    # Manifest = source of truth: a sub-floor scheduled owl cannot be constructed.
    from stackowl.exceptions import ManifestValidationError

    with pytest.raises(ManifestValidationError):
        _scheduled_owl("toofast", schedule="every 1m")
