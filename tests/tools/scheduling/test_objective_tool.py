"""ObjectiveTool — the agent-callable producer for standing objectives (1D).

The assistant calls this when the user asks it to hold a standing objective
("keep an eye on X and handle it"). It decomposes it eagerly into ordered
sub-goals (so the user sees the plan BEFORE anything is persisted — AD-26:
no model call in a command handler, so decomposition runs here, upstream of
``submit_command``), captures the durable delivery target, then submits
``scheduling.set_objective`` — never calling ``ObjectiveStore`` directly
(Story 4.7, AD-1).

``scheduling.set_objective`` is declared IRREVERSIBLE (Design Notes), so the
action-policy gate demands step-up for EVERY requester kind, including the
owner: an ordinary call from here gets back ``outcome is None`` (parked
awaiting approval) and creates nothing yet. The actual persistence + the
git-branch-failure path are exercised end-to-end against the command
handler directly in
``tests/objectives/test_set_objective_is_a_command.py``; this file proves
the TOOL's own behavior — decompose-then-submit ordering, the honest
pending-approval payload, and every pre-submit refusal path (still
unaffected: none of them reach ``submit_command`` at all).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

# Registration side effect (mirrors journal/task_events.py's own shape) — the
# scheduling.set_objective CommandSpec/handler. Production gets this for free
# from startup/orchestrator.py's boot import; a test that builds ObjectiveTool
# directly, with no orchestrator boot, needs it explicitly.
import stackowl.objectives.commands  # noqa: F401
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.objectives.store import ObjectiveStore
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.base import ToolResult
from stackowl.tools.scheduling.objective_tool import ObjectiveTool
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def migrated_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "obj_tool.db"
    seed_schema(db_path)
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
    ttoken = TraceContext.start(session_key="sess-obj-1", interactive=True, channel="cli")
    try:
        return await ObjectiveTool().execute(**kwargs)
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)


def _payload(result: ToolResult) -> dict[str, Any]:
    return json.loads(result.output)


async def test_create_decomposes_then_submits_pending_approval(migrated_db: DbPool) -> None:
    """scheduling.set_objective is IRREVERSIBLE (Design Notes), so an owner's
    own call parks awaiting step-up rather than completing instantly —
    Decomposition already ran (in the tool, upstream of submit_command:
    AD-26), so the plan is surfaced honestly even though nothing is
    persisted yet."""
    pr = _provider_registry("fetch the page\ndiff against last\nreport changes")
    result = await _run(
        migrated_db, provider_registry=pr, intent="watch the page and report changes"
    )
    assert result.success
    body = _payload(result)
    assert body["created"] is False
    assert body["pending_approval"] is True
    assert body["subgoals"] == ["fetch the page", "diff against last", "report changes"]
    assert result.side_effect_committed is False
    objective_id = body["objective_id"]

    # Nothing persisted yet — the command is parked, not run.
    store = ObjectiveStore(migrated_db)
    assert await store.list_objectives() == []

    rows = await migrated_db.fetch_all(
        "SELECT status FROM tasks WHERE kind = 'command' "
        "AND command_type = 'scheduling.set_objective'"
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "parked"
    assert objective_id  # a fresh id was minted regardless of parking


async def test_empty_intent_is_structured_error(migrated_db: DbPool) -> None:
    result = await _run(migrated_db, provider_registry=_provider_registry("x"), intent="   ")
    assert not result.success
    assert "intent" in (result.error or "").lower()


async def test_no_db_is_structured_error(monkeypatch: pytest.MonkeyPatch) -> None:
    result = await _run(None, provider_registry=_provider_registry("x"), intent="do the thing")
    assert not result.success  # degrades, never raises


async def test_decompose_fallback_still_creates_single_step(migrated_db: DbPool) -> None:
    # No standard provider → decomposer fail-safe to the whole-objective single
    # sub-goal; the tool still submits (never stranded) — pending approval,
    # same as any other irreversible objective creation.
    result = await _run(migrated_db, provider_registry=ProviderRegistry(), intent="resilient objective")
    assert result.success
    body = _payload(result)
    assert body["subgoals"] == ["resilient objective"]
    assert body["pending_approval"] is True


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
    ttoken = TraceContext.start(session_key="sess-obj-plain", interactive=True, channel="cli")
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
        self.calls: list[dict[str, object]] = []

    async def request(self, **kwargs: object) -> bool:
        self.calls.append(kwargs)
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
    ttoken = TraceContext.start(session_key="sess-obj-noninteractive", interactive=False, channel="cli")
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
    ttoken = TraceContext.start(session_key="sess-obj-raise", interactive=True, channel="cli")
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
    ttoken = TraceContext.start(session_key="sess-obj-declined", interactive=True, channel="cli")
    try:
        result = await ObjectiveTool().execute(intent="build a feature", repo=str(repo))
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)
    assert result.success is False
    assert result.side_effect_committed is False
    store = ObjectiveStore(migrated_db)
    assert await store.list_objectives() == []


async def test_repo_bearing_call_consent_carries_reply_target(
    tmp_path: Path, migrated_db: DbPool
) -> None:
    """Story 3.5 — ``_gate_epic_consent`` threads ``reply_target`` off
    ``TraceContext`` into ``gate.policy.request(...)``, exactly like the
    other three consent call sites (shell.py/tool_build.py/owl_build.py). A
    split-mode ``GatewayLink._handle_consent`` hard-denies any consent
    request missing this shape, so this is the one gate for an entire
    unattended objective/epic run — it must not regress silently."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    pr = _provider_registry("story one\nstory two")
    gate = _FakeGate(True)
    token = set_services(
        StepServices(db_pool=migrated_db, provider_registry=pr, consent_gate=gate)
    )
    ttoken = TraceContext.start(
        session_key="sess-obj-reply-target", interactive=True, channel="telegram",
        reply_target=72055773,
    )
    try:
        result = await ObjectiveTool().execute(intent="build a feature", repo=str(repo))
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)
    assert result.success is True
    assert gate.policy.calls and gate.policy.calls[0]["reply_target"] == 72055773


async def test_repo_bearing_call_gate_approved_submits_pending_approval(
    tmp_path: Path, migrated_db: DbPool
) -> None:
    """gate.policy.request() returns True ⇒ decomposition proceeds and the
    tool submits scheduling.set_objective — but that command type is
    IRREVERSIBLE (Design Notes), so even an approved-epic owner call parks
    awaiting step-up: no objective row, no integration branch, UNTIL the
    command actually runs (proven end-to-end, including the branch create,
    by tests/objectives/test_set_objective_is_a_command.py)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    pr = _provider_registry("story one\nstory two")
    token = set_services(
        StepServices(db_pool=migrated_db, provider_registry=pr, consent_gate=_FakeGate(True))
    )
    ttoken = TraceContext.start(session_key="sess-obj-approved", interactive=True, channel="cli")
    try:
        result = await ObjectiveTool().execute(intent="build a feature", repo=str(repo))
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)
    assert result.success is True
    body = _payload(result)
    assert body["created"] is False
    assert body["pending_approval"] is True
    assert result.side_effect_committed is False

    store = ObjectiveStore(migrated_db)
    assert await store.list_objectives() == []
    branches = subprocess.run(
        ["git", "branch"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert f"stackowl/epic-{body['objective_id']}" not in branches
