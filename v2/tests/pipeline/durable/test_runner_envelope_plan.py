"""E2-S3 — runner.run computes + persists task_envelope; fail-open → None."""

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
from stackowl.pipeline.durable.task_runner import DurableTaskRunner
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry

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
        return state.evolve(pipeline_step="done")


class _FakeTool(Tool):
    """Minimal Tool subclass — just enough for ToolRegistry.all() to yield a catalog entry."""

    @property
    def name(self) -> str:
        return "fake_tool"

    @property
    def description(self) -> str:
        return "a fake tool for testing"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )

    async def execute(self, **kwargs: object) -> ToolResult:  # pragma: no cover
        return ToolResult(success=True, output="ok", duration_ms=0.0)


class _StubPlanner:
    def __init__(self, result: BoundsSpec | None) -> None:
        self._r = result

    async def plan(
        self,
        goal: str,
        owl_bounds: BoundsSpec | None,
        catalog: list[tuple[str, str]],
    ) -> BoundsSpec | None:
        return self._r


# ---------------------------------------------------------------------------
# DB-backed store / pool fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "runner_envelope.db"
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


def _tool_registry() -> ToolRegistry:
    tr = ToolRegistry()
    tr.register(_FakeTool())  # type: ignore[arg-type]
    return tr


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


async def test_run_sets_and_persists_envelope(pool: DbPool, monkeypatch: pytest.MonkeyPatch) -> None:
    """runner.run calls the planner and persists the returned envelope to the store + state."""
    env = BoundsSpec(tools=frozenset({"a", "tool_search", "tool_describe"}))
    monkeypatch.setattr(
        "stackowl.pipeline.durable.task_runner.PreflightPlanner",
        lambda *a, **k: _StubPlanner(env),
    )

    bounds = BoundsSpec(tools=frozenset({"a"}))
    store = DurableTaskStore(pool, _OWNER)
    backend = _FakeBackend()
    token = set_services(StepServices(
        owl_registry=_reg(bounds),
        tool_registry=_tool_registry(),
        provider_registry=object(),  # type: ignore[arg-type] — non-None sentinel
    ))
    try:
        final_state, task_id = await DurableTaskRunner(store, backend).run(
            goal="g", state=_state()
        )
    finally:
        reset_services(token)

    # The persisted task must carry the envelope.
    persisted = await store.get(task_id)
    assert persisted.task_envelope == env

    # The backend must have received a state with the envelope stamped on it.
    assert backend.ran_with is not None
    assert backend.ran_with.task_envelope == env


async def test_run_failopen_envelope_none(pool: DbPool, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the planner returns None, task_envelope is None and no exception is raised."""
    monkeypatch.setattr(
        "stackowl.pipeline.durable.task_runner.PreflightPlanner",
        lambda *a, **k: _StubPlanner(None),
    )

    store = DurableTaskStore(pool, _OWNER)
    backend = _FakeBackend()
    token = set_services(StepServices(
        owl_registry=_reg(None),
        tool_registry=_tool_registry(),
        provider_registry=object(),  # type: ignore[arg-type] — non-None sentinel
    ))
    try:
        final_state, task_id = await DurableTaskRunner(store, backend).run(
            goal="g", state=_state()
        )
    finally:
        reset_services(token)

    persisted = await store.get(task_id)
    assert persisted.task_envelope is None

    assert backend.ran_with is not None
    assert backend.ran_with.task_envelope is None


async def test_run_failopen_planner_raises(pool: DbPool, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the planner raises, the runner is fail-open: task is still created (envelope=None)."""

    class _RaisingPlanner:
        async def plan(
            self,
            goal: str,
            owl_bounds: BoundsSpec | None,
            catalog: list[tuple[str, str]],
        ) -> BoundsSpec | None:
            raise RuntimeError("planner exploded")

    monkeypatch.setattr(
        "stackowl.pipeline.durable.task_runner.PreflightPlanner",
        lambda *a, **k: _RaisingPlanner(),
    )

    store = DurableTaskStore(pool, _OWNER)
    backend = _FakeBackend()
    token = set_services(StepServices(
        owl_registry=_reg(None),
        tool_registry=_tool_registry(),
        provider_registry=object(),  # type: ignore[arg-type] — non-None sentinel
    ))
    try:
        # Must NOT raise despite the planner exploding.
        final_state, task_id = await DurableTaskRunner(store, backend).run(
            goal="g", state=_state()
        )
    finally:
        reset_services(token)

    persisted = await store.get(task_id)
    assert persisted.task_envelope is None


async def test_run_no_tool_registry_skips_planner(pool: DbPool, monkeypatch: pytest.MonkeyPatch) -> None:
    """When tool_registry is None the planner gate is skipped; envelope stays None."""
    called = []

    class _TrackingPlanner:
        async def plan(
            self,
            goal: str,
            owl_bounds: BoundsSpec | None,
            catalog: list[tuple[str, str]],
        ) -> BoundsSpec | None:
            called.append(True)
            return BoundsSpec(tools=frozenset({"x"}))

    monkeypatch.setattr(
        "stackowl.pipeline.durable.task_runner.PreflightPlanner",
        lambda *a, **k: _TrackingPlanner(),
    )

    store = DurableTaskStore(pool, _OWNER)
    backend = _FakeBackend()
    token = set_services(StepServices(
        owl_registry=_reg(None),
        # tool_registry deliberately absent
        provider_registry=object(),  # type: ignore[arg-type]
    ))
    try:
        final_state, task_id = await DurableTaskRunner(store, backend).run(
            goal="g", state=_state()
        )
    finally:
        reset_services(token)

    assert not called, "planner should not be invoked when tool_registry is None"
    persisted = await store.get(task_id)
    assert persisted.task_envelope is None


async def test_run_no_provider_registry_skips_planner(pool: DbPool, monkeypatch: pytest.MonkeyPatch) -> None:
    """When provider_registry is None the planner gate is skipped; envelope stays None."""
    called = []

    class _TrackingPlanner:
        async def plan(
            self,
            goal: str,
            owl_bounds: BoundsSpec | None,
            catalog: list[tuple[str, str]],
        ) -> BoundsSpec | None:
            called.append(True)
            return BoundsSpec(tools=frozenset({"x"}))

    monkeypatch.setattr(
        "stackowl.pipeline.durable.task_runner.PreflightPlanner",
        lambda *a, **k: _TrackingPlanner(),
    )

    store = DurableTaskStore(pool, _OWNER)
    backend = _FakeBackend()
    token = set_services(StepServices(
        owl_registry=_reg(None),
        tool_registry=_tool_registry(),
        # provider_registry deliberately absent
    ))
    try:
        final_state, task_id = await DurableTaskRunner(store, backend).run(
            goal="g", state=_state()
        )
    finally:
        reset_services(token)

    assert not called, "planner should not be invoked when provider_registry is None"
    persisted = await store.get(task_id)
    assert persisted.task_envelope is None
