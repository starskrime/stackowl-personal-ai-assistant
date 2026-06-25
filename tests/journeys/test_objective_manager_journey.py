"""End-to-end Objective Manager journey (1F).

Drives the keystone through its REAL components — the producer tool creates +
decomposes an objective, then the driver advances it sub-goal by sub-goal across
ticks until it completes and notifies the owner. No mocks of our own code: only
the LLM provider (decompose) and the pipeline backend (sub-goal execution) are
stubbed, exactly as the live system would supply them.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.notifications.proactive_job import ProactiveDeliveryOutcome
from stackowl.objectives.driver import ObjectiveDriverHandler
from stackowl.objectives.store import ObjectiveStore
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.job import Job
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.scheduling.objective_tool import ObjectiveTool

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "obj_journey.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


class _StubBackend:
    def __init__(self) -> None:
        self.runs = 0

    async def run(self, state: PipelineState) -> PipelineState:
        self.runs += 1
        chunk = ResponseChunk(
            content=f"handled: {state.input_text}", is_final=False, chunk_index=0,
            trace_id=state.trace_id, owl_name=state.owl_name,
        )
        return state.evolve(responses=(chunk,))


class _StubDeliverer:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def deliver_for_job(self, job: Job, *, message: str, category: str, urgency: str = "normal") -> ProactiveDeliveryOutcome:
        self.messages.append(message)
        return ProactiveDeliveryOutcome(rollup="delivered", per_channel={"cli": "delivered"})


def _driver_job() -> Job:
    return Job(
        job_id="objective_driver-seed", handler_name="objective_driver",
        schedule="every 1m", idempotency_key="objective_driver",
        last_run_at=None, next_run_at="2026-06-24T00:00:00+00:00", status="running",
    )


async def test_objective_runs_to_completion_across_ticks(db: DbPool) -> None:
    # 1. The assistant creates a standing objective (it decomposes into 3 steps).
    pr = ProviderRegistry()
    pr.register_mock(
        "mock-standard",
        MockProvider(name="mock-standard", canned_text="fetch the data\nanalyze it\nreport the result"),
        tier="standard",
    )
    token = set_services(StepServices(db_pool=db, provider_registry=pr))
    ttoken = TraceContext.start(session_id="sess-j", interactive=True, channel="cli")
    try:
        created = await ObjectiveTool().execute(intent="keep an eye on the data and report")
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)
    assert created.success
    objective_id = json.loads(created.output)["objective_id"]

    # 2. The driver advances it one sub-goal per tick until complete.
    backend = _StubBackend()
    deliverer = _StubDeliverer()
    driver = ObjectiveDriverHandler(db=db, backend=backend, job_deliverer=deliverer)

    store = ObjectiveStore(db, DEFAULT_PRINCIPAL_ID)
    for _ in range(6):  # generous bound; 3 steps + 1 completion tick
        await driver.execute(_driver_job())
        if (await store.get(objective_id)).status == "done":
            break

    # 3. The objective completed: every sub-goal done, owner notified once.
    obj = await store.get(objective_id)
    assert obj.status == "done"
    subs = await store.list_subgoals(objective_id)
    assert [s.status for s in subs] == ["done", "done", "done"]
    assert backend.runs == 3  # one pipeline drive per sub-goal
    assert len(deliverer.messages) == 1  # exactly one completion ping
    assert "complete" in deliverer.messages[0].lower()
    kinds = [e.kind for e in await store.list_events(objective_id)]
    assert kinds.count("subgoal_done") == 3
    assert "completed" in kinds
