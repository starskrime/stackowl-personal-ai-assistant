"""``owl_schedule`` tool's ``pause``/``resume``/``snooze`` actions run as
COMMAND tasks (Story 4.7).

Every action now calls ``commands/spec/submit.py::submit_command`` (never
``JobScheduler.pause``/``.resume``/``.snooze`` directly — proven
independently by ``tests/commands/spec/test_one_door_tripwires.py``'s AST
scan). ``pause``/``resume`` submit under ``scheduling.pause_owl_job``/
``resume_owl_job`` — DISTINCT command types from cronjob's own
``scheduling.pause_job``/``resume_job`` (the Story 4.2 census's own
authored split), even though both pairs call the identical
``JobScheduler.pause``/``.resume``. ``snooze`` (when the duration parses)
submits ``scheduling.set_owl_schedule``; its unparseable-duration fallback
submits ``scheduling.pause_owl_job`` — the SAME command type the tool's own
``pause`` action uses, never a direct ``JobScheduler.pause`` call.

All three types are REVERSIBLE, so an owner's own call runs AT ONCE — this
file proves the OBSERVABLE behavior (a real ``kind='command'`` task row
lands, the job's status actually changes) mirroring ``tests/tools/
test_cronjob_pause_resume_go_through_commands.py``'s own shape.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

# Registration side effect (mirrors journal/task_events.py's own shape).
import stackowl.scheduler.commands  # noqa: F401
from stackowl.db.pool import DbPool
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.trigger import CronTrigger
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.scheduler.owl_lifecycle import _job_id_for, reconcile_owl_schedules
from stackowl.tools.scheduling.owl_schedule import OwlScheduleTool
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "owl_schedule_commands.db"
    seed_schema(db_path)
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _scheduled_owl(name: str = "brain") -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name, role="researcher", system_prompt="poke me with AI news",
        model_tier="fast", lifecycle="scheduled",
        trigger=CronTrigger(schedule="every 2h", prompt="find AI news"),
    )


async def _setup(db: DbPool) -> tuple[OwlRegistry, str]:
    reg = OwlRegistry()
    reg.register(_scheduled_owl("brain"))
    await reconcile_owl_schedules(reg, db)
    return reg, _job_id_for("brain")


async def _command_rows(db: DbPool, command_type: str) -> list[dict[str, object]]:
    return await db.fetch_all(
        "SELECT command_type, status FROM tasks WHERE kind = 'command' AND command_type = ?",
        (command_type,),
    )


async def test_pause_submits_scheduling_pause_owl_job(db: DbPool) -> None:
    reg, job_id = await _setup(db)
    token = set_services(StepServices(owl_registry=reg, db_pool=db))
    try:
        result = await OwlScheduleTool()(action="pause", name="brain")
    finally:
        reset_services(token)

    assert result.success, result.error
    rows = await db.fetch_all("SELECT enabled FROM jobs WHERE job_id = ?", (job_id,))
    assert int(rows[0]["enabled"]) == 0

    command_rows = await _command_rows(db, "scheduling.pause_owl_job")
    assert len(command_rows) == 1
    assert command_rows[0]["status"] == "completed"
    # Never the cronjob-owned type — the two never merge.
    assert await _command_rows(db, "scheduling.pause_job") == []


async def test_resume_submits_scheduling_resume_owl_job(db: DbPool) -> None:
    reg, job_id = await _setup(db)
    token = set_services(StepServices(owl_registry=reg, db_pool=db))
    try:
        await OwlScheduleTool()(action="pause", name="brain")
        result = await OwlScheduleTool()(action="resume", name="brain")
    finally:
        reset_services(token)

    assert result.success, result.error
    rows = await db.fetch_all("SELECT enabled FROM jobs WHERE job_id = ?", (job_id,))
    assert int(rows[0]["enabled"]) == 1

    command_rows = await _command_rows(db, "scheduling.resume_owl_job")
    assert len(command_rows) == 1
    assert command_rows[0]["status"] == "completed"
    assert await _command_rows(db, "scheduling.resume_job") == []


async def test_snooze_with_parseable_duration_submits_set_owl_schedule(db: DbPool) -> None:
    reg, job_id = await _setup(db)
    before = (await db.fetch_all("SELECT next_run_at FROM jobs WHERE job_id = ?", (job_id,)))[0]
    token = set_services(StepServices(owl_registry=reg, db_pool=db))
    try:
        result = await OwlScheduleTool()(action="snooze", name="brain", snooze_for="8h")
    finally:
        reset_services(token)

    assert result.success, result.error
    after = (await db.fetch_all("SELECT next_run_at FROM jobs WHERE job_id = ?", (job_id,)))[0]
    assert after["next_run_at"] > before["next_run_at"]

    command_rows = await _command_rows(db, "scheduling.set_owl_schedule")
    assert len(command_rows) == 1
    assert command_rows[0]["status"] == "completed"


async def test_snooze_without_duration_falls_back_to_the_command_driven_pause(
    db: DbPool,
) -> None:
    """The unparseable-duration fallback submits scheduling.pause_owl_job —
    the SAME command type the tool's own `pause` action uses — never a
    direct JobScheduler.pause call."""
    reg, job_id = await _setup(db)
    token = set_services(StepServices(owl_registry=reg, db_pool=db))
    try:
        result = await OwlScheduleTool()(action="snooze", name="brain")
    finally:
        reset_services(token)

    assert result.success, result.error
    rows = await db.fetch_all("SELECT enabled FROM jobs WHERE job_id = ?", (job_id,))
    assert int(rows[0]["enabled"]) == 0

    command_rows = await _command_rows(db, "scheduling.pause_owl_job")
    assert len(command_rows) == 1
    assert command_rows[0]["status"] == "completed"
    assert await _command_rows(db, "scheduling.set_owl_schedule") == []
