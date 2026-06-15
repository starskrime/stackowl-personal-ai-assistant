"""J-DURABLE JOURNEY — "an owl goal runs durably, exactly-once, and is delivered".

The gateway-driven integration smoke for durable-ReAct **B3**: route an owl GOAL
through the durable pipeline (gated by ``settings.durable.goals``) and prove the
fresh durable-goal path end-to-end with ONLY the AI provider mocked.

The drive is the GENUINE user→goal→dispatch path: a goal job is persisted exactly
as :class:`~stackowl.commands.agent_create_command.AgentCreateCommand` persists it
(``JobScheduler.create_job(handler_name="goal_execution", ...)``), then the REAL
scheduler dispatch entry (``JobScheduler.run_now``) claims and runs it through the
REAL :class:`~stackowl.scheduler.handlers.goal_execution.GoalExecutionHandler` →
REAL :class:`~stackowl.pipeline.backends.asyncio_backend.AsyncioBackend` → REAL
pipeline → the B2-aware execute step → the REAL durable ledger/checkpoint seams.

REAL (everything except the AI): the migrated :class:`DbPool` (tmp_db), the whole
pipeline, the :class:`ToolRegistry` with a registered ``write``-severity tool, the
real :class:`JobScheduler` + ``jobs``/``job_runs`` tables, the real
:class:`DurableTaskStore` + ``tasks`` table, the real
:class:`~stackowl.pipeline.durable.ledger.SideEffectLedger` + ``side_effect_ledger``
table, the real per-iteration checkpoint callback. FAKED: ONLY the AI provider
(scripted, owl-aware, multi-iteration tool-using — like the existing journey tests).

Business-outcome assertions (NOT tool return-shapes):

  FLAG ON
    1. The goal's answer is DELIVERED to the user: the scheduler dispatch returns a
       successful :class:`JobResult` whose ``output`` carries the model's final
       answer, and a ``job_results`` row records it — what ``/agents log`` surfaces.
    2. A ``DurableTask`` row was created and ends ``completed`` (the goal genuinely
       ran durably to terminal state).
    3. A checkpoint row exists (``tasks.checkpoint_blob`` is non-NULL) — each ReAct
       iteration was persisted, so a crash could resume.
    4. A ``side_effect_ledger`` row exists, ``committed``, for the goal's
       side-effecting tool — the exactly-once seam is LIVE on the goal path.

  FLAG OFF (proving the gate)
    5. The SAME goal runs ephemerally: the answer is still delivered (JobResult +
       job_results), but NO ``tasks`` row and NO ``side_effect_ledger`` row are
       written — durability is genuinely gated off.

NOTE on scope (FR13 follow-up): a true user→gateway→goal-creation path DOES exist
(``/agent-create`` → ``AgentCreateCommand`` → ``scheduler.create_job`` →
``goal_execution`` job). It persists a job the scheduler later dispatches; the goal
runs under ``DEFAULT_PRINCIPAL_ID`` (there is no per-user goal ASSIGNMENT yet — that
multi-tenant owner thread-point is the documented FR13 follow-up). This smoke drives
the genuine persisted-job → scheduler-dispatch path (``run_now``), which is exactly
what a due cron tick / ``/agents run`` invokes — only the polling-loop timer is
skipped. The full kill+resume gateway journey lands at B4.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest

from stackowl.config.settings import DurableSettings, Settings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult
from stackowl.providers.react_callback import IterationCallback, ReActIterationState
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.handlers.goal_execution import GoalExecutionHandler
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry

_GOAL = "Write the status note and report back"
_FINAL_ANSWER = "Done — I wrote the status note for you."
_TOOL_ARGS: dict[str, Any] = {"path": "status.txt", "content": "all systems green"}


# --- the ONLY mock: the secretary owl's scripted multi-iteration provider --------


class _StatusNoteTool(Tool):
    """A ``write``-severity tool: side-effecting (ledger-guarded under a durable
    drive) but NOT consequential (needs no consent gate to run)."""

    def __init__(self) -> None:
        self.runs = 0

    @property
    def name(self) -> str:
        return "write_note"

    @property
    def description(self) -> str:
        return "write a status note (side-effecting)"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.runs += 1
        return ToolResult(success=True, output="NOTE WRITTEN", error=None, duration_ms=1.0)


class _ScriptedSecretary:
    """The ONLY mock — stands in for the secretary owl's LLM.

    Within a SINGLE ``complete_with_tools`` call it drives the REAL tool loop via
    the REAL ``tool_dispatcher`` (exactly as a real model would): dispatch the
    side-effecting tool, fire the per-iteration checkpoint callback for iteration 0
    (only supplied on the durable path), then return the final answer.
    """

    def __init__(self) -> None:
        self.dispatch_result: str | None = None

    @property
    def name(self) -> str:
        return "scripted-secretary"

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
        wrapup_deadline_s: float | None = None,  # F027/SP-4 — match the real signature
    ) -> tuple[str, list[dict[str, Any]]]:
        self.dispatch_result = await tool_dispatcher("write_note", dict(_TOOL_ARGS))
        records = [{"name": "write_note", "args": dict(_TOOL_ARGS), "result": self.dispatch_result}]
        if on_iteration_complete is not None:
            await on_iteration_complete(
                ReActIterationState(
                    iteration=0,
                    messages=[{"role": "assistant", "content": "called write_note"}],
                    tool_call_records=records,
                )
            )
        return _FINAL_ANSWER, records

    async def complete(self, *a: object, **k: object) -> CompletionResult:
        # The triage SecretaryRouter calls this to pick the owl; returning
        # "secretary" keeps the goal on the secretary owl (where our scripted
        # tool-loop runs). This is routing only — the real tool drive happens in
        # complete_with_tools above.
        return CompletionResult(
            content="secretary",
            input_tokens=1,
            output_tokens=1,
            model="",
            provider_name=self.name,
            duration_ms=0.0,
        )

    async def stream(self, *a: object, **k: object) -> AsyncIterator[str]:  # pragma: no cover
        if False:
            yield ""


class _FakeProviderRegistry:
    def __init__(self, p: _ScriptedSecretary) -> None:
        self._p = p

    def get(self, name: str) -> _ScriptedSecretary:
        return self._p

    def get_by_tier(self, tier: str) -> _ScriptedSecretary:
        return self._p

    def get_with_cascade(self, tier: str) -> _ScriptedSecretary:
        return self._p


# --- fixtures -------------------------------------------------------------------


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "durable_goal.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    """The handler asserts not-test-mode; this smoke drives the REAL path."""
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _clean_handler_registry():  # noqa: ANN202
    HandlerRegistry.reset()
    yield
    HandlerRegistry.reset()


def _settings(*, durable_goals: bool) -> Settings:
    # The handler reads ONLY ``settings.durable.goals``. ``Settings`` is a
    # BaseSettings whose source order drops constructor kwargs (env/yaml win), so —
    # like the other journey tests — wrap a REAL frozen ``DurableSettings`` (a plain
    # BaseModel that DOES honour its kwarg) in a namespace cast to ``Settings``.
    return cast(Settings, SimpleNamespace(durable=DurableSettings(goals=durable_goals)))


def _backend(
    pool: DbPool, provider: _ScriptedSecretary, tool: _StatusNoteTool
) -> AsyncioBackend:
    reg = ToolRegistry()
    reg.register(tool)
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=reg,
        stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
        db_pool=pool,  # REAL DbPool — the durable execute step reads this
    )
    return AsyncioBackend(services=services)


async def _make_goal_job(scheduler: JobScheduler) -> str:
    """Persist a goal job exactly as AgentCreateCommand → create_job does."""
    job = await scheduler.create_job(
        handler_name="goal_execution",
        schedule="daily@09:00",
        params={"goal": _GOAL},
    )
    return job.job_id


async def _drive(
    pool: DbPool, *, durable_goals: bool
) -> tuple[Any, _ScriptedSecretary, _StatusNoteTool]:
    """Persist a goal + dispatch it through the REAL scheduler entry (run_now).

    Returns the scheduler's :class:`JobResult` (what the user is delivered), the
    scripted provider, and the side-effecting tool INSTANCE (so a test can assert
    ``tool.runs`` — the exactly-once execution-count proof). ONLY the AI provider
    is faked.
    """
    provider = _ScriptedSecretary()
    tool = _StatusNoteTool()
    backend = _backend(pool, provider, tool)
    handler = GoalExecutionHandler(
        backend=backend, db=pool, settings=_settings(durable_goals=durable_goals),
    )
    HandlerRegistry.instance().register(handler)

    scheduler = JobScheduler(db=pool)
    job_id = await _make_goal_job(scheduler)
    # REAL scheduler dispatch — claims the persisted job (CAS), runs the real
    # handler, persists job_runs. Exactly what a due cron tick / /agents run does.
    result = await scheduler.run_now(job_id)
    return result, provider, tool


async def _tasks(pool: DbPool) -> list[dict[str, Any]]:
    return await pool.fetch_all(
        "SELECT task_id, goal, status, checkpoint_blob, result FROM tasks", ()
    )


async def _ledger(pool: DbPool) -> list[dict[str, Any]]:
    return await pool.fetch_all(
        "SELECT status, tool_name FROM side_effect_ledger", ()
    )


async def _job_results(pool: DbPool) -> list[dict[str, Any]]:
    return await pool.fetch_all(
        "SELECT status, result_text FROM job_results", ()
    )


# --- FLAG ON: the fresh durable-goal path runs end-to-end -----------------------


async def test_durable_goal_runs_durably_and_is_delivered(pool: DbPool) -> None:
    result, provider, tool = await _drive(pool, durable_goals=True)

    # OUTCOME 1 — the goal's answer is DELIVERED to the user (JobResult + history).
    assert result is not None, "scheduler did not dispatch the goal"
    assert result.success is True, f"goal failed: {result.error!r}"
    assert result.output == _FINAL_ANSWER, (
        f"OUTCOME 1 FAIL: the model's answer was not delivered. Got: {result.output!r}"
    )
    job_results = await _job_results(pool)
    assert any(
        r["status"] == "completed" and r["result_text"] == _FINAL_ANSWER
        for r in job_results
    ), f"OUTCOME 1 FAIL: no completed job_results row carrying the answer. {job_results}"

    # OUTCOME 2 — a DurableTask was created and ended 'completed'.
    tasks = await _tasks(pool)
    assert len(tasks) == 1, f"OUTCOME 2 FAIL: expected exactly one durable task. {tasks}"
    task = tasks[0]
    assert task["goal"] == _GOAL
    assert task["status"] == "completed", (
        f"OUTCOME 2 FAIL: durable task did not end completed. status={task['status']!r}"
    )
    assert task["result"] == _FINAL_ANSWER

    # OUTCOME 3 — a checkpoint was persisted (the ReAct iteration was durable).
    assert task["checkpoint_blob"] is not None, (
        "OUTCOME 3 FAIL: no checkpoint persisted — the drive was not durable."
    )

    # OUTCOME 4 — the side-effecting tool is ledger-guarded, committed (exactly-once).
    ledger = await _ledger(pool)
    assert len(ledger) == 1, f"OUTCOME 4 FAIL: expected one ledger row. {ledger}"
    assert ledger[0]["status"] == "committed"
    assert ledger[0]["tool_name"] == "write_note"
    # The side-effecting tool actually ran (the answer is real, not canned).
    assert provider.dispatch_result == "NOTE WRITTEN"
    # OUTCOME 4 (execution-count proof) — the side effect executed EXACTLY ONCE.
    # The ledger row + the answer prove a run happened; this proves it did not run
    # more than once under the durable guard.
    assert tool.runs == 1, (
        f"OUTCOME 4 FAIL: exactly-once violated — tool ran {tool.runs} times."
    )


# --- FLAG OFF: the SAME goal runs ephemerally (proving the gate) ----------------


async def test_goal_runs_ephemerally_when_flag_off(pool: DbPool) -> None:
    result, provider, tool = await _drive(pool, durable_goals=False)

    # OUTCOME 5a — the answer is STILL delivered (the gate only changes durability).
    assert result is not None
    assert result.success is True, f"goal failed: {result.error!r}"
    assert result.output == _FINAL_ANSWER
    job_results = await _job_results(pool)
    assert any(
        r["status"] == "completed" and r["result_text"] == _FINAL_ANSWER
        for r in job_results
    ), f"OUTCOME 5a FAIL: ephemeral goal answer not delivered. {job_results}"

    # OUTCOME 5b — NOTHING durable was written: no task row, no ledger row.
    assert await _tasks(pool) == [], (
        "OUTCOME 5b FAIL: a durable task was created with the flag OFF — gate leaked."
    )
    assert await _ledger(pool) == [], (
        "OUTCOME 5b FAIL: a side-effect ledger row was written with the flag OFF."
    )
    # The tool still ran EXACTLY ONCE (just not ledger-guarded) — the gate changes
    # durability, never the execution count.
    assert provider.dispatch_result == "NOTE WRITTEN"
    assert tool.runs == 1, (
        f"OUTCOME 5 FAIL: ephemeral goal ran the tool {tool.runs} times (want 1)."
    )


# --- PARK: a replay-uncertain side effect parks the task end-to-end -------------


async def test_durable_goal_parks_when_side_effect_replay_uncertain(
    pool: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A durable goal whose side-effecting step already has an ``intent`` row with
    NO matching ``commit`` must PARK end-to-end, NOT re-run the side effect.

    Pre-seed the ledger with an ``intent``-without-``commit`` row for the goal's
    ``write_note`` step (exactly the state a crash mid-side-effect leaves behind).
    When the durable drive reaches that step the guard returns ``uncertain`` →
    raises → the execute step marks ``durable_parked`` → DurableTaskRunner
    finalizes the task ``parked``. We assert the task row ends ``parked`` (the
    B3 handler→runner park routing works end-to-end) and the side-effecting tool
    NEVER ran (a half-done side effect is never blindly re-executed).

    The runtime task_id is made deterministic by patching the runner's id source
    so the pre-seeded ledger row keys onto the SAME (task, step, tool, args) the
    durable drive computes.
    """
    from stackowl.pipeline.durable import task_runner as _runner_mod
    from stackowl.pipeline.durable.ledger import SideEffectLedger
    from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

    # Make the runner's generated task_id deterministic so the pre-seed lands on
    # the exact (task, step=0, tool, args) the durable guard will look up.
    fixed_hex = "abc123def456"
    monkeypatch.setattr(
        _runner_mod.uuid, "uuid4", lambda: SimpleNamespace(hex=fixed_hex + "0" * 20)
    )
    task_id = f"task-{fixed_hex}"

    # Pre-seed the intent-without-commit row for the side-effecting step (iter 0).
    # begin() with no prior row writes a fresh `intent` and returns "proceed" — that
    # IS the half-done state; we never commit it, so the real drive sees it as a
    # prior intent → "uncertain".
    ledger = SideEffectLedger(pool, DEFAULT_PRINCIPAL_ID)
    decision = await ledger.begin(task_id, 0, "write_note", dict(_TOOL_ARGS))
    assert decision.outcome == "proceed", "pre-seed should write a fresh intent row"

    result, provider, tool = await _drive(pool, durable_goals=True)

    # OUTCOME P1 — the durable task row ends 'parked' (NOT 'failed'): the B3
    # handler→runner park routing fired end-to-end.
    tasks = await _tasks(pool)
    assert len(tasks) == 1, f"OUTCOME P1 FAIL: expected one task. {tasks}"
    assert tasks[0]["status"] == "parked", (
        f"OUTCOME P1 FAIL: task did not park. status={tasks[0]['status']!r}"
    )

    # OUTCOME P2 — the side effect was NOT re-run (a half-done effect is never
    # blindly replayed). The pre-seeded intent is still the only ledger row and it
    # stayed 'intent' (uncommitted).
    assert tool.runs == 0, (
        f"OUTCOME P2 FAIL: side effect re-ran on an uncertain replay ({tool.runs}x)."
    )
    ledger_rows = await _ledger(pool)
    assert len(ledger_rows) == 1 and ledger_rows[0]["status"] == "intent", (
        f"OUTCOME P2 FAIL: ledger row was mutated on park. {ledger_rows}"
    )

    # OUTCOME P3 — the user-facing signal is an UNAMBIGUOUS park, not a bare
    # failure: JobResult surfaces parked + the job_results row records "parked".
    assert result.success is False
    assert result.output is not None and "PARKED" in result.output, (
        f"OUTCOME P3 FAIL: parked goal output is not clearly parked. {result.output!r}"
    )
    assert result.metadata.get("parked") is True
    job_results = await _job_results(pool)
    assert any(r["status"] == "parked" for r in job_results), (
        f"OUTCOME P3 FAIL: no 'parked' job_results row. {job_results}"
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
