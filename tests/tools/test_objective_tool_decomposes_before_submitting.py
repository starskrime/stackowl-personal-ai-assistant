"""``objective`` tool decomposes BEFORE submitting (Story 4.7, AD-26: "no
model call in a handler"). Decomposition (an LLM call) runs in
``objective_tool.execute()``, upstream of ``submit_command`` — never inside
``objectives/commands.py``'s handler.

This file proves the observable consequences: an invalid EPIC dependency
graph is refused before ANY command task row exists (no create-then-abandon,
the strict improvement Design Notes call out), a decomposer failure never
reaches ``submit_command`` either, and the submitted command's own payload
already carries the fully decomposed sub-goals.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

import stackowl.objectives.commands  # noqa: F401 -- registration side effect
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.objectives.decomposer import ObjectiveDecomposer
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
    db_path = tmp_path / "obj_decompose_before_submit.db"
    seed_schema(db_path)
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _provider_registry(canned: str) -> ProviderRegistry:
    reg = ProviderRegistry()
    reg.register_mock(
        "mock-standard", MockProvider(name="mock-standard", canned_text=canned), tier="standard",
    )
    return reg


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True)
    (path / "f.txt").write_text("x")
    subprocess.run(["git", "add", "f.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


class _FakePolicy:
    """Minimal stand-in for ConsentPolicy — only .request() matters here
    (mirrors tests/tools/scheduling/test_objective_tool.py's own fixture)."""

    def __init__(self, outcome: bool) -> None:
        self._outcome = outcome

    async def request(self, **kwargs: object) -> bool:
        return self._outcome


class _FakeGate:
    def __init__(self, outcome: bool) -> None:
        self.policy = _FakePolicy(outcome)


async def _run(
    db: DbPool, *, provider_registry: ProviderRegistry | None = None,
    consent_gate: object | None = None, **kwargs: object,
) -> ToolResult:
    token = set_services(
        StepServices(db_pool=db, provider_registry=provider_registry, consent_gate=consent_gate)  # type: ignore[arg-type]
    )
    ttoken = TraceContext.start(session_key="sess-decompose-1", interactive=True, channel="cli")
    try:
        return await ObjectiveTool().execute(**kwargs)
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)


async def _command_task_rows(db: DbPool) -> list[dict[str, object]]:
    return await db.fetch_all(
        "SELECT command_payload FROM tasks WHERE kind = 'command' "
        "AND command_type = 'scheduling.set_objective'"
    )


async def test_invalid_epic_dependency_graph_is_refused_before_any_command_exists(
    tmp_path: Path, migrated_db: DbPool,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    # story 0 depends on 1, story 1 depends on 0 -- a two-node cycle.
    pr = _provider_registry("story one <<depends-on: 1>>\nstory two <<depends-on: 0>>")

    result = await _run(
        migrated_db, provider_registry=pr, intent="build a feature", repo=str(repo),
        consent_gate=_FakeGate(True),
    )

    assert result.success is False
    assert "dependency graph" in (result.error or "")
    assert result.side_effect_committed is False

    store = ObjectiveStore(migrated_db)
    assert await store.list_objectives() == []
    assert await _command_task_rows(migrated_db) == [], (
        "an invalid dependency graph must never reach submit_command at all"
    )


async def test_decomposer_failure_never_reaches_submit_command(
    migrated_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(self: ObjectiveDecomposer, intent: str) -> list[object]:
        raise RuntimeError("decomposition exploded")

    monkeypatch.setattr(ObjectiveDecomposer, "decompose_specs", _boom)
    pr = _provider_registry("irrelevant")

    result = await _run(migrated_db, provider_registry=pr, intent="a plain objective")

    assert result.success is False
    assert "decomposition" in (result.error or "").lower()
    assert await _command_task_rows(migrated_db) == []


async def test_the_submitted_command_payload_carries_the_already_decomposed_subgoals(
    migrated_db: DbPool,
) -> None:
    """The command task's OWN payload already holds the decomposed
    sub-goals — proving decomposition ran BEFORE submit_command, not inside
    its handler (which never calls a model, AD-26)."""
    pr = _provider_registry("fetch the page\ndiff against last\nreport changes")

    result = await _run(migrated_db, provider_registry=pr, intent="watch a page")

    assert result.success is True
    rows = await _command_task_rows(migrated_db)
    assert len(rows) == 1
    payload = json.loads(rows[0]["command_payload"])
    assert [s["description"] for s in payload["subgoals"]] == [
        "fetch the page", "diff against last", "report changes",
    ]
