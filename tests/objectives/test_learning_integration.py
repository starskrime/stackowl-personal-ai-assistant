"""Objective work feeds the learning loop (Phase 5).

The keystone reused the outcome-capturing pipeline backend instead of a parallel
path, so every objective sub-goal driven through ``AsyncioBackend.run`` writes a
``task_outcomes`` row (with a DNA snapshot) — exactly the rows the existing
critic_scorer → reflection_writer → tool_outcome_miner → skill_synthesizer chain
and DNA attribution already consume. This locks that integration so a future
refactor can't silently sever autonomous objective work from learning.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.objectives.driver import ObjectiveDriverHandler
from stackowl.objectives.model import Objective
from stackowl.objectives.store import ObjectiveStore
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.scheduler.job import Job
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "obj_learning.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _driver_job() -> Job:
    return Job(
        job_id="objective_driver-seed", handler_name="objective_driver",
        schedule="every 1m", idempotency_key="objective_driver",
        last_run_at=None, next_run_at="2026-06-24T00:00:00+00:00", status="running",
    )


async def test_objective_subgoal_writes_a_task_outcome(db: DbPool) -> None:
    # An objective with one sub-goal, advanced through the REAL pipeline backend.
    store = ObjectiveStore(db, DEFAULT_PRINCIPAL_ID)
    await store.create(Objective(
        objective_id="obj-learn", owner_id=DEFAULT_PRINCIPAL_ID,
        intent="watch and report", channel="cli",
    ))
    await store.add_subgoals("obj-learn", ["analyze the logs"])

    # Stub the pipeline steps to no-ops so the backend runs fast but still takes
    # its real outcome-capture path at the end of run().
    from stackowl.pipeline import registry as reg_module
    from stackowl.pipeline.steps import deliver as deliver_module

    async def _noop(state: PipelineState) -> PipelineState:
        return state

    orig_steps = list(reg_module.PIPELINE_STEPS)
    orig_deliver = deliver_module.run
    reg_module.PIPELINE_STEPS[:] = [("triage", _noop), ("execute", _noop)]
    deliver_module.run = _noop  # type: ignore[assignment]
    try:
        backend = AsyncioBackend(services=StepServices(db_pool=db))
        driver = ObjectiveDriverHandler(db=db, backend=backend)  # settings=None → ephemeral
        await driver.execute(_driver_job())
    finally:
        reg_module.PIPELINE_STEPS[:] = orig_steps
        deliver_module.run = orig_deliver  # type: ignore[assignment]

    # The sub-goal's run was captured as a task_outcome under the objective's
    # session — the same rows the critic/reflection/miner/DNA-attribution consume.
    outcomes = await TaskOutcomeStore(db).recent_for_session("objective-obj-learn")
    assert len(outcomes) == 1
    assert outcomes[0].input_text == "analyze the logs"
