"""J1/J2 KILL+RESUME — the durable-ReAct capstone (B4 crash recovery).

The end-to-end proof that a durable goal interrupted by a process crash RESUMES
to completion and runs its side-effecting tool EXACTLY ONCE across the crash —
the J1 (durable resume) + J2 (exactly-once side-effects) journeys at the
user-outcome level, with ONLY the AI provider mocked.

The journey, in two acts driven through the REAL durable seams (REAL DbPool,
REAL DurableTaskStore + ``tasks`` table, REAL SideEffectLedger +
``side_effect_ledger`` table, REAL per-iteration checkpoint callback, REAL
AsyncioBackend + pipeline + ToolRegistry):

  ACT 1 — CRASH mid-drive
    A durable goal runs. Iteration 0 does no side effect and checkpoints (so the
    last persisted checkpoint is iteration 0). Iteration 1 dispatches a
    side-effecting tool — the ledger COMMITS the effect at step_index 1 — and
    THEN the scripted provider raises (simulating a process kill AFTER the
    side-effect committed but BEFORE the iteration-1 checkpoint / completion).
    This is the genuine crash window the ledger protects: a committed effect with
    NO checkpoint past it. A crash leaves the row orphaned, so we force it back to
    ``running`` to model the on-disk state a killed process leaves: ``running`` +
    a committed ledger row at step_index 1 + a checkpoint only up to iteration 0.

  ACT 2 — RECOVER
    ``recover_durable_tasks(db, backend2)`` runs with a SECOND scripted provider.
    The recovery seeds ``ctx.iteration = checkpoint.iteration + 1 = 1`` (re-enter
    the interrupted iteration 1). It RE-ATTEMPTS the side-effecting tool at
    step_index 1 — which now hits the committed ledger row (``already_committed``,
    replayed, NOT re-run) — then continues to the final answer.

  ASSERT (J1 + J2 user-outcomes)
    * The side-effecting tool ran EXACTLY ONCE across crash + recovery
      (``tool.runs == 1`` — the recovery re-attempt hit ``already_committed``).
    * The DurableTask ends ``completed``.
    * The final answer is delivered (the recovered run's final state carries it).
    * The ledger holds exactly ONE committed row for that step.

This is the durable-completion + exactly-once capstone: a crash cannot lose the
goal and cannot double-run its side effect.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.durable.recovery import recover_durable_tasks
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task_runner import DurableTaskRunner
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult
from stackowl.providers.react_callback import IterationCallback, ReActIterationState
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry

_GOAL = "Send the report and confirm"
_FINAL_ANSWER = "Done — the report was sent and confirmed."
_TOOL_ARGS: dict[str, Any] = {"to": "ops", "body": "quarterly report"}


class _Crash(RuntimeError):
    """Simulated process-kill: raised AFTER commit + checkpoint, before done."""


class _SideEffectTool(Tool):
    """A ``write``-severity (side-effecting, ledger-guarded) tool with a run counter.

    The SAME instance is shared across the crashed drive AND the recovery drive,
    so ``runs`` is the cross-crash execution count — the exactly-once proof.
    """

    def __init__(self) -> None:
        self.runs = 0

    @property
    def name(self) -> str:
        return "send_report"

    @property
    def description(self) -> str:
        return "send the report (side-effecting)"

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
        return ToolResult(success=True, output="REPORT SENT", error=None, duration_ms=1.0)


class _CrashingProvider:
    """ACT 1 provider: checkpoint iter 0, dispatch the side-effect in iter 1, crash.

    The crash falls in the genuine exactly-once window: AFTER the iteration-1
    side-effect commits at step_index 1 but BEFORE the iteration-1 checkpoint, so
    the last persisted checkpoint is iteration 0 and the committed ledger row at
    step_index 1 has no checkpoint past it.
    """

    def __init__(self) -> None:
        self.dispatched = False

    @property
    def name(self) -> str:
        return "scripted-crashing"

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
        # ITERATION 0 — no side effect; complete it so a checkpoint at iter 0 is
        # persisted (the last durable cursor before the crash). ctx.iteration 0->1.
        if on_iteration_complete is not None:
            await on_iteration_complete(
                ReActIterationState(
                    iteration=0,
                    messages=[
                        {"role": "user", "content": user_text},
                        {"role": "assistant", "content": "thinking about the report"},
                    ],
                    tool_call_records=[],
                )
            )
        # ITERATION 1 — run the side-effecting tool. The durable ctx.iteration is
        # now 1, so the ledger COMMITS this effect at step_index 1.
        await tool_dispatcher("send_report", dict(_TOOL_ARGS))
        self.dispatched = True
        # CRASH: killed AFTER the commit but BEFORE the iteration-1 checkpoint —
        # the committed effect has no checkpoint past it (the real crash window).
        raise _Crash("simulated process kill after iter-1 side effect commit")

    async def complete(self, *a: object, **k: object) -> CompletionResult:
        return CompletionResult(
            content="secretary", input_tokens=1, output_tokens=1, model="",
            provider_name=self.name, duration_ms=0.0,
        )

    async def stream(self, *a: object, **k: object) -> AsyncIterator[str]:  # pragma: no cover
        if False:
            yield ""


class _RecoveringProvider:
    """ACT 2 provider: re-attempt the side-effect (replayed), then finish.

    The recovery resume seeds ``resume_messages`` from the iter-0 checkpoint and
    ``ctx.iteration = 1`` (re-enter the interrupted iteration 1). This provider
    re-dispatches the SAME side-effecting tool (a real model would, having the
    transcript) at step_index 1 — the ledger must short-circuit it to
    ``already_committed`` so the tool does NOT run a second time — then completes
    iteration 1 and returns the final answer.
    """

    def __init__(self) -> None:
        self.saw_resume_iteration: int | None = None
        self.replay_result: str | None = None

    @property
    def name(self) -> str:
        return "scripted-recovering"

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
        # Prove the resume context was forwarded (B1/B2): the recovered drive
        # carries the iter-1 transcript, not a fresh start.
        self.saw_resume_iteration = (
            len(resume_messages) if resume_messages is not None else None
        )
        # Re-attempt the side-effecting step — the ledger must REPLAY it.
        self.replay_result = await tool_dispatcher("send_report", dict(_TOOL_ARGS))
        records = [
            {"name": "send_report", "args": dict(_TOOL_ARGS), "result": self.replay_result}
        ]
        if on_iteration_complete is not None:
            await on_iteration_complete(
                ReActIterationState(
                    iteration=1,
                    messages=[{"role": "assistant", "content": "confirmed"}],
                    tool_call_records=records,
                )
            )
        return _FINAL_ANSWER, records

    async def complete(self, *a: object, **k: object) -> CompletionResult:
        return CompletionResult(
            content="secretary", input_tokens=1, output_tokens=1, model="",
            provider_name=self.name, duration_ms=0.0,
        )

    async def stream(self, *a: object, **k: object) -> AsyncIterator[str]:  # pragma: no cover
        if False:
            yield ""


class _SimpleFinishingProvider:
    """A provider that completes in one iteration with no side effect.

    Used by the stale-``recovering`` reclaim test: the orphan has no checkpoint
    and no ledgered effect, so recovery just needs to drive it to a clean
    ``completed`` final answer.
    """

    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "scripted-simple"

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
        self.calls += 1
        if on_iteration_complete is not None:
            await on_iteration_complete(
                ReActIterationState(
                    iteration=0,
                    messages=[{"role": "assistant", "content": "done"}],
                    tool_call_records=[],
                )
            )
        return _FINAL_ANSWER, []

    async def complete(self, *a: object, **k: object) -> CompletionResult:
        return CompletionResult(
            content="secretary", input_tokens=1, output_tokens=1, model="",
            provider_name=self.name, duration_ms=0.0,
        )

    async def stream(self, *a: object, **k: object) -> AsyncIterator[str]:  # pragma: no cover
        if False:
            yield ""


class _FakeProviderRegistry:
    def __init__(self, p: object) -> None:
        self._p = p

    def get(self, name: str) -> object:
        return self._p

    def get_by_tier(self, tier: str) -> object:
        return self._p

    def get_with_cascade(self, tier: str) -> object:
        return self._p


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "durable_kill_resume.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    """The durable drive asserts not-test-mode; this smoke drives the REAL path."""
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _backend(pool: DbPool, provider: object, tool: _SideEffectTool) -> AsyncioBackend:
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


def _durable_state() -> PipelineState:
    return PipelineState(
        trace_id="kill-resume",
        session_id="kill-resume",
        input_text=_GOAL,
        channel="cli",
        owl_name="secretary",
        pipeline_step="",
        interactive=False,
    )


async def _tasks(pool: DbPool) -> list[dict[str, Any]]:
    return await pool.fetch_all(
        "SELECT task_id, status, checkpoint_blob, result FROM tasks", ()
    )


async def _ledger(pool: DbPool) -> list[dict[str, Any]]:
    return await pool.fetch_all(
        "SELECT status, tool_name FROM side_effect_ledger", ()
    )


async def test_durable_goal_survives_crash_and_runs_side_effect_exactly_once(
    pool: DbPool,
) -> None:
    # SHARED across both acts — the cross-crash execution-count proof.
    tool = _SideEffectTool()

    # ---- ACT 1 — CRASH mid-drive --------------------------------------------
    crashing = _CrashingProvider()
    backend1 = _backend(pool, crashing, tool)
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    runner = DurableTaskRunner(store, backend1)

    # The crash surfaces INSIDE the pipeline's tool loop: the execute step
    # records it as a state error and the drive returns (the runner finalizes the
    # orphan 'failed'). Either way the on-disk effect is what matters: a committed
    # ledger row + an iter-1 checkpoint left behind by a process that never
    # completed the goal.
    final_state, _ = await runner.run(goal=_GOAL, state=_durable_state())
    assert final_state.errors, "ACT 1 precondition: the crash must surface as a state error"

    assert crashing.dispatched, "ACT 1 precondition: the side effect must have run"
    assert tool.runs == 1, f"ACT 1 precondition: side effect ran {tool.runs}x (want 1)"

    tasks = await _tasks(pool)
    assert len(tasks) == 1, f"ACT 1: expected one durable task. {tasks}"
    task_id = tasks[0]["task_id"]
    assert tasks[0]["checkpoint_blob"] is not None, (
        "ACT 1: a checkpoint must be persisted (the crash was after the callback)"
    )
    ledger = await _ledger(pool)
    assert len(ledger) == 1 and ledger[0]["status"] == "committed", (
        f"ACT 1: the side effect must be COMMITTED in the ledger. {ledger}"
    )

    # Model the on-disk state a KILLED process leaves: the row is orphaned in
    # 'running' (the live coroutine died before any finalize would have run) AND
    # its 'result' column is NULL. A real kill leaves result NULL — but ACT 1's
    # in-process fail-loud finalize wrote a stale "pipeline error: ..." there. Null
    # it back so the post-recovery assertion that result == the REAL final answer
    # genuinely proves RECOVERY wrote it (not a leftover from the crashed drive).
    await pool.execute(
        "UPDATE tasks SET status = 'running', result = NULL WHERE task_id = ?",
        (task_id,),
    )
    pre = await _tasks(pool)
    assert pre[0]["result"] is None, (
        f"ACT 1->2 precondition: result must be NULL like a real kill. {pre[0]['result']!r}"
    )

    # ---- ACT 2 — RECOVER -----------------------------------------------------
    recovering = _RecoveringProvider()
    backend2 = _backend(pool, recovering, tool)  # SAME tool instance

    # Recovery now LAUNCHES the resume drive in the BACKGROUND (so a real startup
    # is not blocked by serial ReAct drives). The fast claim+reconstruct pass is
    # awaited here; drain() awaits the background drive to a terminal outcome
    # before we assert the user-visible result.
    recoverer = await recover_durable_tasks(pool, backend2)

    # The orphan was claimed + its drive LAUNCHED by THIS sweep.
    assert recoverer.launched == 1, (
        f"recovery should have launched exactly one drive, got {recoverer.launched}"
    )
    await recoverer.drain()
    assert recoverer.in_flight == 0, "all background drives should have drained"
    # The resume genuinely continued mid-transcript (B1/B2 resume_* forwarded).
    assert recovering.saw_resume_iteration is not None and recovering.saw_resume_iteration > 0, (
        "recovery did not forward the checkpoint transcript into the provider"
    )
    # The recovery re-attempted the side effect and got the REPLAYED result.
    assert recovering.replay_result == "REPORT SENT", (
        f"recovery replay result wrong: {recovering.replay_result!r}"
    )

    # ---- J2 — EXACTLY-ONCE across crash + recovery ---------------------------
    assert tool.runs == 1, (
        f"J2 FAIL: exactly-once violated — side effect ran {tool.runs}x across "
        "crash + recovery (the recovery re-attempt should have hit already_committed)."
    )
    ledger_after = await _ledger(pool)
    assert len(ledger_after) == 1, (
        f"J2 FAIL: expected exactly ONE committed ledger row for the step. {ledger_after}"
    )
    assert ledger_after[0]["status"] == "committed"
    assert ledger_after[0]["tool_name"] == "send_report"

    # ---- J1 — durable completion + delivery ----------------------------------
    tasks_after = await _tasks(pool)
    assert len(tasks_after) == 1
    assert tasks_after[0]["status"] == "completed", (
        f"J1 FAIL: recovered task did not complete. status={tasks_after[0]['status']!r}"
    )
    # The final answer is delivered (the runner finalized the task with it).
    assert tasks_after[0]["result"] == _FINAL_ANSWER, (
        f"J1 FAIL: final answer not delivered. result={tasks_after[0]['result']!r}"
    )


async def test_recovery_is_a_noop_when_no_orphans(pool: DbPool) -> None:
    """Non-durable / clean startup: no ``running`` tasks => recovery is a no-op.

    Proves the wiring never changes clean-startup behavior — with nothing
    orphaned, recovery touches nothing and returns 0.
    """
    tool = _SideEffectTool()
    backend = _backend(pool, _RecoveringProvider(), tool)

    recoverer = await recover_durable_tasks(pool, backend)
    await recoverer.drain()

    assert recoverer.launched == 0, "recovery must launch nothing when there are no orphans"
    assert recoverer.in_flight == 0, "no background drives when nothing to recover"
    assert await _tasks(pool) == [], "recovery must not create any task rows"
    assert tool.runs == 0, "recovery must not run any tool when there is nothing to recover"


async def test_stale_recovering_orphan_is_reclaimed_not_stuck_forever(
    pool: DbPool,
) -> None:
    """A task stuck in ``recovering`` at startup is RECLAIMED — not orphaned.

    Models the gap a process can die in: it won the claim (running ->
    ``recovering``) but was killed BEFORE it could resume. The prior process is
    dead, so at the NEXT startup that ``recovering`` row is a STALE orphan with no
    live drive behind it. The old sweep listed only ``running`` and would have
    left it ``recovering`` FOREVER; the hardened sweep must reclaim it and drive
    it to a terminal status.

    This test FAILS before the running+recovering sweep/claim fix (the orphan is
    never listed nor claimable, so it stays ``recovering``) and PASSES after.
    """
    from datetime import UTC, datetime

    from stackowl.pipeline.durable.task import DurableTask

    tool = _SideEffectTool()
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)

    # Create a durable task, then force it to 'recovering' via direct SQL to model
    # the exact mid-claim kill state (claimed but never resumed). No checkpoint and
    # no ledger row — a fresh resume drives it to completion.
    now = datetime.now(tz=UTC)
    task_id = "task-stale-recover"
    await store.create(
        DurableTask(
            task_id=task_id,
            owner_id=DEFAULT_PRINCIPAL_ID,
            goal=_GOAL,
            status="running",
            owl_name="secretary",
            channel="cli",
            created_at=now,
            updated_at=now,
        )
    )
    await pool.execute(
        "UPDATE tasks SET status = 'recovering' WHERE task_id = ?", (task_id,)
    )
    stuck = await _tasks(pool)
    assert stuck[0]["status"] == "recovering", "precondition: task must be 'recovering'"

    # ---- RECOVER -------------------------------------------------------------
    backend = _backend(pool, _SimpleFinishingProvider(), tool)
    recoverer = await recover_durable_tasks(pool, backend)

    # The stale 'recovering' orphan was reclaimed + its drive launched.
    assert recoverer.launched == 1, (
        f"stale 'recovering' orphan must be reclaimed, launched={recoverer.launched}"
    )
    await recoverer.drain()

    # It is NOT stuck 'recovering' forever — it reached a terminal status.
    tasks_after = await _tasks(pool)
    assert len(tasks_after) == 1
    assert tasks_after[0]["status"] == "completed", (
        f"stale 'recovering' orphan was not finalized — status={tasks_after[0]['status']!r} "
        "(it would be stuck 'recovering' forever without the running+recovering sweep fix)"
    )
    assert tasks_after[0]["result"] == _FINAL_ANSWER, (
        f"recovery did not write the final answer. result={tasks_after[0]['result']!r}"
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
