"""E2-S2 — runner.run snapshots the acting owl's bounds into the task + state."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from stackowl.authz.bounds import BoundsSpec
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.durable.task_runner import DurableTaskRunner
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState

if TYPE_CHECKING:
    from stackowl.pipeline.backends.base import OrchestratorBackend

# ---------------------------------------------------------------------------
# Minimal doubles
# ---------------------------------------------------------------------------

_OWNER = "principal-default"


class _FakeBackend:
    """OrchestratorBackend double that captures the PipelineState it was run with."""

    ran_with: PipelineState | None = None

    async def run(self, state: PipelineState) -> PipelineState:  # noqa: D102
        self.ran_with = state
        # Return a minimal completed state (no errors, no parked).
        return state.evolve(pipeline_step="done")


# ---------------------------------------------------------------------------
# DB-backed store pool fixture (same pattern as test_task_store.py)
# ---------------------------------------------------------------------------


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "runner_ceiling.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reg(bounds: BoundsSpec | None) -> OwlRegistry:
    r = OwlRegistry()
    r.register(OwlAgentManifest(
        name="o",
        role="r",
        system_prompt="s",
        model_tier="fast",
        bounds=bounds,
    ))
    return r


def _state() -> PipelineState:
    return PipelineState(
        trace_id="t",
        session_id="s",
        input_text="g",
        channel="cli",
        owl_name="o",
        pipeline_step="",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_run_snapshots_owl_bounds(pool: DbPool) -> None:
    """runner.run persists creation_ceiling from owl registry and threads it into state."""
    bounds = BoundsSpec(tools=frozenset({"a"}))
    store = DurableTaskStore(pool, _OWNER)
    backend = _FakeBackend()
    token = set_services(StepServices(owl_registry=_reg(bounds)))
    try:
        await DurableTaskRunner(store, backend).run(goal="g", state=_state())
    finally:
        reset_services(token)

    # The store must have exactly one task, and it must carry the ceiling.
    tasks = await store.list()
    assert len(tasks) == 1
    persisted = tasks[0]
    assert persisted.creation_ceiling == bounds

    # The backend must have received a state with the ceiling stamped on it.
    assert backend.ran_with is not None
    assert backend.ran_with.creation_ceiling == bounds


async def test_run_unbounded_owl_snapshots_none(pool: DbPool) -> None:
    """runner.run with an unbounded owl persists None ceiling and threads None into state."""
    store = DurableTaskStore(pool, _OWNER)
    backend = _FakeBackend()
    token = set_services(StepServices(owl_registry=_reg(None)))
    try:
        await DurableTaskRunner(store, backend).run(goal="g", state=_state())
    finally:
        reset_services(token)

    tasks = await store.list()
    assert len(tasks) == 1
    assert tasks[0].creation_ceiling is None

    assert backend.ran_with is not None
    assert backend.ran_with.creation_ceiling is None


async def test_run_no_registry_snapshots_none(pool: DbPool) -> None:
    """runner.run with no owl_registry is best-effort: snapshots None ceiling."""
    store = DurableTaskStore(pool, _OWNER)
    backend = _FakeBackend()
    # No owl_registry wired — get_services() returns empty StepServices.
    token = set_services(StepServices())
    try:
        await DurableTaskRunner(store, backend).run(goal="g", state=_state())
    finally:
        reset_services(token)

    tasks = await store.list()
    assert len(tasks) == 1
    assert tasks[0].creation_ceiling is None

    assert backend.ran_with is not None
    assert backend.ran_with.creation_ceiling is None
