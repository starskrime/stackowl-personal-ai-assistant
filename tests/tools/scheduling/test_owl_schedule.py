"""TS11 — natural-language STOP / SNOOZE / RESUME for a scheduled owl (pause != delete).

The owl_schedule tool is the user's recoverable off-ramp: it toggles the owl's projected
scheduler row (``enabled``) via the existing :class:`JobScheduler` lifecycle methods —
it NEVER deletes the owl. The critical invariant (d): a user pause must SURVIVE a
reconcile pass (reconcile re-enables only on a real manifest edit, not on a pure pause).

Intent recognition is the owl LLM mapping NL → a structured ``action`` (multilingual,
no English wordlist); these tests drive the deterministic effect directly.
"""

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
from stackowl.tools.scheduling.owl_schedule import OwlScheduleTool

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


def _scheduled_owl(name: str = "brain", *, schedule: str = "every 2h") -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name, role="researcher", system_prompt="poke me with AI news",
        model_tier="fast", lifecycle="scheduled",
        trigger=CronTrigger(schedule=schedule, prompt="find AI news"),
    )


async def _enabled(db: DbPool, job_id: str) -> int:
    rows = await db.fetch_all("SELECT enabled FROM jobs WHERE job_id = ?", (job_id,))
    assert rows, f"no job row {job_id}"
    return int(rows[0]["enabled"])


async def _setup(db: DbPool) -> tuple[OwlRegistry, str]:
    reg = OwlRegistry()
    reg.register(_scheduled_owl("brain"))
    await reconcile_owl_schedules(reg, db)  # projects the owned job row (enabled=1)
    return reg, _job_id_for("brain")


async def test_c_pause_disables_job_and_keeps_owl(db: DbPool) -> None:
    """(c) pause → the projected job is enabled=0, and the owl still exists."""
    reg, job_id = await _setup(db)
    token = set_services(StepServices(owl_registry=reg, db_pool=db))
    try:
        result = await OwlScheduleTool()(action="pause", name="brain")
    finally:
        reset_services(token)
    assert result.success, result.error
    assert await _enabled(db, job_id) == 0  # paused (recoverable)
    assert any(m.name == "brain" for m in reg.all())  # owl NOT deleted


async def test_d_pause_survives_reconcile(db: DbPool) -> None:
    """(d) the durability invariant — a reconcile after a user pause must NOT
    re-enable the job (the owl manifest is unchanged → owned-row no-op)."""
    reg, job_id = await _setup(db)
    token = set_services(StepServices(owl_registry=reg, db_pool=db))
    try:
        await OwlScheduleTool()(action="pause", name="brain")
    finally:
        reset_services(token)
    assert await _enabled(db, job_id) == 0
    # Reconcile (boot / next owl change) must respect the pause.
    result = await reconcile_owl_schedules(reg, db)
    assert result.created == 0 and result.updated == 0  # no-op on the paused row
    assert await _enabled(db, job_id) == 0, "reconcile re-enabled a user-paused job!"


async def test_e_resume_re_enables_job(db: DbPool) -> None:
    """(e) resume → the job is enabled=1 again."""
    reg, job_id = await _setup(db)
    token = set_services(StepServices(owl_registry=reg, db_pool=db))
    try:
        await OwlScheduleTool()(action="pause", name="brain")
        assert await _enabled(db, job_id) == 0
        result = await OwlScheduleTool()(action="resume", name="brain")
    finally:
        reset_services(token)
    assert result.success, result.error
    assert await _enabled(db, job_id) == 1  # back on


async def test_snooze_keeps_enabled_and_pushes_next_run(db: DbPool) -> None:
    """snooze with a parseable duration auto-resumes: enabled stays 1, next_run is pushed."""
    reg, job_id = await _setup(db)
    before = (await db.fetch_all("SELECT next_run_at FROM jobs WHERE job_id = ?", (job_id,)))[0]
    token = set_services(StepServices(owl_registry=reg, db_pool=db))
    try:
        result = await OwlScheduleTool()(action="snooze", name="brain", snooze_for="8h")
    finally:
        reset_services(token)
    assert result.success, result.error
    assert await _enabled(db, job_id) == 1  # snooze auto-resumes → stays enabled
    after = (await db.fetch_all("SELECT next_run_at FROM jobs WHERE job_id = ?", (job_id,)))[0]
    assert after["next_run_at"] > before["next_run_at"]  # pushed into the future


async def test_snooze_without_duration_falls_back_to_pause(db: DbPool) -> None:
    """snooze with no/unparseable duration honestly degrades to a pause (TS11 note)."""
    reg, job_id = await _setup(db)
    token = set_services(StepServices(owl_registry=reg, db_pool=db))
    try:
        result = await OwlScheduleTool()(action="snooze", name="brain")
    finally:
        reset_services(token)
    assert result.success, result.error
    assert await _enabled(db, job_id) == 0  # degraded to pause
    assert "paused" in result.output.lower()


async def test_unknown_owl_is_honest_not_a_silent_success(db: DbPool) -> None:
    reg, _ = await _setup(db)
    token = set_services(StepServices(owl_registry=reg, db_pool=db))
    try:
        result = await OwlScheduleTool()(action="pause", name="nobody")
    finally:
        reset_services(token)
    assert result.success is False
    assert "nobody" in (result.error or "")
