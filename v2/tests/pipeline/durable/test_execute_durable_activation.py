"""B2 — durable activation in the pipeline execute step.

Drives the REAL execute step (`_run_with_tools`) with a scripted multi-iteration
tool-using provider double (the ONLY thing mocked is the AI provider) over a REAL
SQLite DbPool (MigrationRunner, no DB mocks). Proves:

* DEFAULT PATH (task_id=None): the provider call runs with NO active
  DurableReActContext (get_active() is None during the call) and writes nothing
  to the tasks/ledger tables — byte-for-byte current behavior.
* DURABLE PATH (task_id set + a real DbPool + a pre-created DurableTask): during
  the provider call get_active() returns the ctx; a side-effecting tool dispatched
  in the loop is ledger-guarded (a side_effect_ledger row appears), a checkpoint
  is persisted, and on a simulated re-run the committed side-effect is NOT
  repeated (exactly-once at the execute-step level).
* FAIL-LOUD: task_id set but no DbPool wired → the step raises rather than running
  a "durable" task non-durably.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.context import get_active
from stackowl.pipeline.durable.ledger import SideEffectLedger
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.providers.react_callback import IterationCallback, ReActIterationState
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry

_TOOL_ARGS: dict[str, Any] = {"path": "out.txt", "content": "hello"}


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "execute_durable.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def _state(**kwargs: object) -> PipelineState:
    base: dict[str, object] = {
        "trace_id": "tr",
        "session_id": "se",
        "input_text": "do the thing",
        "channel": "cli",
        "owl_name": "secretary",
        "pipeline_step": "execute",
    }
    base.update(kwargs)
    return PipelineState(**base)  # type: ignore[arg-type]


class _SideEffectTool(Tool):
    """A `write`-severity tool that records how many times it actually ran."""

    def __init__(self) -> None:
        self.runs = 0

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "write a file (side-effecting)"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        # `write` severity → side-effecting → ledger-guarded under a durable ctx,
        # but NOT consequential, so it needs no consent gate to run.
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.runs += 1
        return ToolResult(success=True, output="WROTE", error=None, duration_ms=1.0)


class _ScriptedProvider:
    """Scripted multi-iteration tool-using provider double.

    Iteration 0: dispatch the side-effecting tool once, then (if a durable
    callback was supplied) fire `on_iteration_complete` for iteration 0. Then
    return a final answer. Records the active durable context observed DURING the
    dispatch so the test can assert dormant-vs-active.
    """

    def __init__(self) -> None:
        self.active_during_call: object | None = "UNSET"
        self.dispatch_result: str | None = None

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "anthropic"

    async def complete_with_tools(
        self,
        user_text: str,
        system_text: str | None,
        tool_schemas: list[dict[str, Any]],
        tool_dispatcher: Callable[[str, dict[str, Any]], Awaitable[str]],
        max_iterations: int = 8,
        history: list[Any] | None = None,
        persistence_check: Callable[[str, list[str]], Awaitable[str | None]] | None = None,
        on_iteration_complete: IterationCallback | None = None,
        resume_messages: list[dict[str, Any]] | None = None,
        resume_tool_calls: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        # Observe the durable context that is live during the loop body.
        self.active_during_call = get_active()
        # Iteration 0 — dispatch the side-effecting tool once.
        self.dispatch_result = await tool_dispatcher("write_file", dict(_TOOL_ARGS))
        records = [{"name": "write_file", "args": dict(_TOOL_ARGS), "result": self.dispatch_result}]
        # Fire the per-iteration checkpoint callback (only present on the durable path).
        if on_iteration_complete is not None:
            await on_iteration_complete(
                ReActIterationState(
                    iteration=0,
                    messages=[{"role": "assistant", "content": "called write_file"}],
                    tool_call_records=records,
                )
            )
        return "done", records


def _registry() -> tuple[ToolRegistry, _SideEffectTool]:
    reg = ToolRegistry()
    tool = _SideEffectTool()
    reg.register(tool)
    return reg, tool


async def _make_task(pool: DbPool, task_id: str, owner_id: str) -> None:
    now = datetime.now(tz=UTC)
    store = DurableTaskStore(pool, owner_id)
    await store.create(
        DurableTask(
            task_id=task_id,
            owner_id=owner_id,
            goal="durable goal",
            status="running",
            created_at=now,
            updated_at=now,
        )
    )


async def _ledger_rows(pool: DbPool) -> list[dict[str, Any]]:
    return await pool.fetch_all(
        "SELECT idempotency_key, status, tool_name FROM side_effect_ledger", ()
    )


# ---- DEFAULT PATH (task_id=None) ---------------------------------------------


async def test_default_path_no_durable_context_and_no_rows(pool: DbPool) -> None:
    reg, tool = _registry()
    provider = _ScriptedProvider()
    token = set_services(StepServices(db_pool=pool))
    try:
        out = await _run_with_tools(_state(task_id=None), provider, reg)  # type: ignore[arg-type]
    finally:
        reset_services(token)

    # No durable context was active during the provider call.
    assert provider.active_during_call is None
    # The tool ran (default dispatch path), but NOT ledger-guarded.
    assert tool.runs == 1
    assert provider.dispatch_result == "WROTE"
    # Nothing durable was written.
    assert await _ledger_rows(pool) == []
    tasks = await DurableTaskStore(pool, "principal-default").list()
    assert tasks == []
    # State carries the final response.
    assert any(c.content == "done" for c in out.responses)


# ---- DURABLE PATH (task_id set) ----------------------------------------------


async def test_durable_path_activates_guards_and_checkpoints(pool: DbPool) -> None:
    owner = "principal-default"
    await _make_task(pool, "task-1", owner)

    reg, tool = _registry()
    provider = _ScriptedProvider()
    token = set_services(StepServices(db_pool=pool))
    try:
        await _run_with_tools(_state(task_id="task-1"), provider, reg)  # type: ignore[arg-type]
    finally:
        reset_services(token)

    # During the provider call the durable context was active and pointed at task-1.
    assert provider.active_during_call is not None
    assert getattr(provider.active_during_call, "task_id", None) == "task-1"
    # The side-effecting tool ran exactly once and was ledger-guarded (committed row).
    assert tool.runs == 1
    rows = await _ledger_rows(pool)
    assert len(rows) == 1
    assert rows[0]["status"] == "committed"
    assert rows[0]["tool_name"] == "write_file"
    # A checkpoint was persisted and current_step advanced to 1.
    store = DurableTaskStore(pool, owner)
    assert await store.load_checkpoint("task-1") is not None
    assert (await store.get("task-1")).current_step == 1


async def test_durable_rerun_is_exactly_once(pool: DbPool) -> None:
    owner = "principal-default"
    await _make_task(pool, "task-2", owner)

    reg, tool = _registry()
    token = set_services(StepServices(db_pool=pool))
    try:
        # First drive — the side effect runs and commits.
        await _run_with_tools(_state(task_id="task-2"), _ScriptedProvider(), reg)  # type: ignore[arg-type]
        assert tool.runs == 1
        # Simulated re-run (crash/replay): SAME task, SAME iteration-0 dispatch.
        provider2 = _ScriptedProvider()
        await _run_with_tools(_state(task_id="task-2"), provider2, reg)  # type: ignore[arg-type]
    finally:
        reset_services(token)

    # The committed side effect was NOT repeated — exactly-once at the step level.
    assert tool.runs == 1
    # The replayed dispatch still returned the recorded output.
    assert provider2.dispatch_result is not None
    assert "WROTE" in provider2.dispatch_result
    # Still exactly one ledger row.
    assert len(await _ledger_rows(pool)) == 1


# ---- PARK (durable replay uncertain → structured park signal) ----------------


async def test_durable_uncertain_parks_and_does_not_rerun(pool: DbPool) -> None:
    """An `intent` row without a commit forces `uncertain` → the step PARKS.

    Pre-seed an `intent` ledger row for the SAME (task, iteration=0, tool, args)
    the scripted provider will dispatch (begin() writes the intent, we never
    commit). On the drive, the dispatch re-begins that key, the ledger returns
    `uncertain`, ledger_guard raises DurableReplayUncertain, and the execute step
    catches it as a STRUCTURED park: state.durable_parked is True, the tool did
    NOT re-run (runs==0), and a 'durable:park:uncertain' marker lands in errors —
    distinct from a transient failure so the B3 router can decide park-vs-retry.
    """
    owner = "principal-default"
    await _make_task(pool, "task-park", owner)

    # Pre-seed the intent (begin writes 'intent', returns proceed; no commit).
    ledger = SideEffectLedger(pool, owner)
    decision = await ledger.begin("task-park", 0, "write_file", dict(_TOOL_ARGS))
    assert decision.outcome == "proceed"

    reg, tool = _registry()
    provider = _ScriptedProvider()
    token = set_services(StepServices(db_pool=pool))
    try:
        out = await _run_with_tools(_state(task_id="task-park"), provider, reg)  # type: ignore[arg-type]
    finally:
        reset_services(token)

    # Structured park signal (not a stringified transient error).
    assert out.durable_parked is True
    # A clear park marker is recorded in errors.
    assert any(e.startswith("durable:park:uncertain:") for e in out.errors)
    assert any("task=task-park" in e for e in out.errors)
    # The side-effecting tool was NOT re-run (the guard refused the half-done call).
    assert tool.runs == 0
    # No second ledger row was created — still exactly the one pre-seeded intent.
    rows = await _ledger_rows(pool)
    assert len(rows) == 1
    assert rows[0]["status"] == "intent"


# ---- FAIL-LOUD (durable task, no DbPool) -------------------------------------


async def test_durable_without_db_pool_fails_loud() -> None:
    reg, tool = _registry()
    provider = _ScriptedProvider()
    token = set_services(StepServices(db_pool=None))  # no DbPool wired
    try:
        out = await _run_with_tools(_state(task_id="task-x"), provider, reg)  # type: ignore[arg-type]
    finally:
        reset_services(token)
    # The step catches the raise into state.errors (never silently runs non-durably).
    assert any("task-x" in e or "DbPool" in e or "durability" in e for e in out.errors)
    # The provider loop never ran, so the tool never ran.
    assert tool.runs == 0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
