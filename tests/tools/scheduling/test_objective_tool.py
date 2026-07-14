"""ObjectiveTool — the agent-callable producer for standing objectives (1D).

The assistant calls this when the user asks it to hold a standing objective
("keep an eye on X and handle it"). It creates the objective, decomposes it
eagerly into ordered sub-goals (so the user sees the plan), captures the durable
delivery target, and persists everything — the driver then advances it.
"""

from __future__ import annotations

import json
import subprocess
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


# --------------------------------------------------------------- epic (repo=)


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True)
    (path / "f.txt").write_text("x")
    subprocess.run(["git", "add", "f.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


async def test_repo_bearing_call_requires_consent(
    tmp_path: Path, migrated_db: DbPool
) -> None:
    """No consent gate wired (StepServices.consent_gate defaults to None, exactly
    the ambient state _run() below builds) ⇒ fail closed, epic never created —
    proven by a real store.list_objectives() spy, not just the ToolResult flags."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    pr = _provider_registry("story one\nstory two")
    result = await _run(migrated_db, provider_registry=pr, intent="build a feature", repo=str(repo))
    assert result.success is False
    assert result.side_effect_committed is False
    store = ObjectiveStore(migrated_db)
    assert await store.list_objectives() == []


async def test_repo_bearing_call_consent_summary_discloses_bypass_permissions(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    summary = ObjectiveTool().consent_summary(intent="build a feature", repo=str(repo))
    assert summary is not None
    assert str(repo) in summary
    assert "bypassPermissions" in summary


async def test_plain_objective_call_untouched(migrated_db: DbPool) -> None:
    """No repo ⇒ no consent gate consulted at all (byte-identical to today).

    A consent_gate that raises on first touch proves the plain path never even
    reads services.consent_gate — not just that no prompt happened to fire.
    """

    class _ExplodingGate:
        @property
        def policy(self) -> object:
            raise AssertionError("consent gate must not be touched for a plain objective call")

    pr = _provider_registry("step one\nstep two")
    token = set_services(
        StepServices(db_pool=migrated_db, provider_registry=pr, consent_gate=_ExplodingGate())  # type: ignore[arg-type]
    )
    ttoken = TraceContext.start(session_id="sess-obj-plain", interactive=True, channel="cli")
    try:
        result = await ObjectiveTool().execute(intent="plain objective, no repo")
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)
    assert result.success is True


class _FakePolicy:
    """Minimal stand-in for ConsentPolicy — only .request() matters here."""

    def __init__(self, outcome: bool | BaseException) -> None:
        self._outcome = outcome

    async def request(self, **kwargs: object) -> bool:
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


class _FakeGate:
    def __init__(self, outcome: bool | BaseException) -> None:
        self.policy = _FakePolicy(outcome)


async def test_repo_bearing_call_non_interactive_refused(
    tmp_path: Path, migrated_db: DbPool
) -> None:
    """interactive=False ⇒ refused before the gate is even consulted, no DB write."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    pr = _provider_registry("story one\nstory two")
    token = set_services(
        StepServices(db_pool=migrated_db, provider_registry=pr, consent_gate=_FakeGate(True))
    )
    ttoken = TraceContext.start(session_id="sess-obj-noninteractive", interactive=False, channel="cli")
    try:
        result = await ObjectiveTool().execute(intent="build a feature", repo=str(repo))
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)
    assert result.success is False
    assert result.side_effect_committed is False
    store = ObjectiveStore(migrated_db)
    assert await store.list_objectives() == []


async def test_repo_bearing_call_gate_raises_refused(
    tmp_path: Path, migrated_db: DbPool
) -> None:
    """gate.policy.request() raising ⇒ caught, logged, refused — never propagated,
    no DB write."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    pr = _provider_registry("story one\nstory two")
    token = set_services(
        StepServices(
            db_pool=migrated_db, provider_registry=pr,
            consent_gate=_FakeGate(RuntimeError("policy exploded")),
        )
    )
    ttoken = TraceContext.start(session_id="sess-obj-raise", interactive=True, channel="cli")
    try:
        result = await ObjectiveTool().execute(intent="build a feature", repo=str(repo))
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)
    assert result.success is False
    assert result.side_effect_committed is False
    store = ObjectiveStore(migrated_db)
    assert await store.list_objectives() == []


async def test_repo_bearing_call_gate_declined_refused(
    tmp_path: Path, migrated_db: DbPool
) -> None:
    """gate.policy.request() returns False ⇒ declined, no DB write."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    pr = _provider_registry("story one\nstory two")
    token = set_services(
        StepServices(db_pool=migrated_db, provider_registry=pr, consent_gate=_FakeGate(False))
    )
    ttoken = TraceContext.start(session_id="sess-obj-declined", interactive=True, channel="cli")
    try:
        result = await ObjectiveTool().execute(intent="build a feature", repo=str(repo))
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)
    assert result.success is False
    assert result.side_effect_committed is False
    store = ObjectiveStore(migrated_db)
    assert await store.list_objectives() == []


async def test_repo_bearing_call_gate_approved_creates_epic(
    tmp_path: Path, migrated_db: DbPool
) -> None:
    """gate.policy.request() returns True ⇒ proceeds: integration branch created
    in the real repo, objective persisted with repo/base_branch/integration_branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    pr = _provider_registry("story one\nstory two")
    token = set_services(
        StepServices(db_pool=migrated_db, provider_registry=pr, consent_gate=_FakeGate(True))
    )
    ttoken = TraceContext.start(session_id="sess-obj-approved", interactive=True, channel="cli")
    try:
        result = await ObjectiveTool().execute(intent="build a feature", repo=str(repo))
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)
    assert result.success is True
    store = ObjectiveStore(migrated_db)
    objs = await store.list_objectives()
    assert len(objs) == 1
    assert objs[0].repo == str(repo)
    assert objs[0].integration_branch == f"stackowl/epic-{objs[0].objective_id}"
    assert objs[0].base_branch in ("main", "master")
    branches = subprocess.run(
        ["git", "branch"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert objs[0].integration_branch in branches
