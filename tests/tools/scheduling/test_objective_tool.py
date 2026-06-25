"""ObjectiveTool — the agent-callable producer for standing objectives (1D).

The assistant calls this when the user asks it to hold a standing objective
("keep an eye on X and handle it"). It creates the objective, decomposes it
eagerly into ordered sub-goals (so the user sees the plan), captures the durable
delivery target, and persists everything — the driver then advances it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.objectives.store import ObjectiveStore
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.base import ToolResult
from stackowl.tools.scheduling.objective_tool import ObjectiveTool

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def migrated_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "obj_tool.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _provider_registry(canned: str) -> ProviderRegistry:
    reg = ProviderRegistry()
    reg.register_mock("mock-standard", MockProvider(name="mock-standard", canned_text=canned), tier="standard")
    return reg


async def _run(
    db: DbPool | None, *, provider_registry: ProviderRegistry | None = None, **kwargs: object
) -> ToolResult:
    token = set_services(StepServices(db_pool=db, provider_registry=provider_registry))
    ttoken = TraceContext.start(session_id="sess-obj-1", interactive=True, channel="cli")
    try:
        return await ObjectiveTool().execute(**kwargs)
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)


def _payload(result: ToolResult) -> dict[str, Any]:
    return json.loads(result.output)


async def test_create_persists_objective_and_decomposes(migrated_db: DbPool) -> None:
    pr = _provider_registry("fetch the page\ndiff against last\nreport changes")
    result = await _run(
        migrated_db, provider_registry=pr, intent="watch the page and report changes"
    )
    assert result.success
    body = _payload(result)
    assert body["created"] is True
    objective_id = body["objective_id"]
    assert body["subgoals"] == ["fetch the page", "diff against last", "report changes"]

    # Persisted + reloadable via a fresh store (proves DB write).
    store = ObjectiveStore(migrated_db)
    obj = await store.get(objective_id)
    assert obj.intent == "watch the page and report changes"
    assert obj.status == "active"
    subs = await store.list_subgoals(objective_id)
    assert [s.description for s in subs] == ["fetch the page", "diff against last", "report changes"]
    kinds = [e.kind for e in await store.list_events(objective_id)]
    assert "created" in kinds and "decomposed" in kinds


async def test_empty_intent_is_structured_error(migrated_db: DbPool) -> None:
    result = await _run(migrated_db, provider_registry=_provider_registry("x"), intent="   ")
    assert not result.success
    assert "intent" in (result.error or "").lower()


async def test_no_db_is_structured_error(monkeypatch: pytest.MonkeyPatch) -> None:
    result = await _run(None, provider_registry=_provider_registry("x"), intent="do the thing")
    assert not result.success  # degrades, never raises


async def test_decompose_fallback_still_creates_single_step(migrated_db: DbPool) -> None:
    # No standard provider → decomposer fail-safe to the whole-objective single
    # sub-goal; the objective is still created (never stranded).
    result = await _run(migrated_db, provider_registry=ProviderRegistry(), intent="resilient objective")
    assert result.success
    body = _payload(result)
    assert body["subgoals"] == ["resilient objective"]


async def test_manifest_severity_and_group() -> None:
    m = ObjectiveTool().manifest
    assert m.name == "objective"
    assert m.action_severity == "write"
    assert m.toolset_group == "scheduling"


async def test_registered_in_with_defaults() -> None:
    from stackowl.tools.registry import ToolRegistry

    registry = ToolRegistry.with_defaults()
    assert any(t.name == "objective" for t in registry.all())
