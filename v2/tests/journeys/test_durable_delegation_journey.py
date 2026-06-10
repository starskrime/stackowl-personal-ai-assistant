"""MERGE-GATE — durable delegated child runs write W exactly once across a crash (D1 §11.2).

A durable PARENT delegates a sub-task to a write-capable CHILD. The child performs
a transactional write W (ledgered under the child's OWN child_task_id). The parent
process crashes BEFORE its delegate_task ledger entry / completion commits. Startup
recovery resumes the ROOT parent, which re-delegates → re-derives the SAME
child_task_id → the child's write W replays (already_committed) instead of
double-firing. W ran EXACTLY ONCE and the parent goal completes.

REAL components throughout (DbPool, DurableTaskStore + tasks, SideEffectLedger +
side_effect_ledger, AsyncioBackend + pipeline + ToolRegistry, A2ADelegator,
OwlRegistry with a write-capable specialist). Only the AI provider is scripted.
This is the D1 capstone.

The two acts, mirroring ``test_j1_j2_durable_kill_resume.py`` but with the
side-effect run by a DELEGATED durable CHILD instead of the durable task directly:

  ACT 1 — CRASH after the child's write W committed
    The durable PARENT (a root) runs. Its provider dispatches ``delegate_task`` to
    the write-capable specialist. The child runs ITS OWN durable sub-pipeline (its
    sub_state carries the derived child_task_id), dispatches ``file_report`` —
    which the child's ledger COMMITS under the child_task_id — and returns ok. The
    parent's delegate_task tool resolves + terminalizes the child ``completed``,
    then the parent provider RAISES (_Crash): a process kill AFTER the child write
    committed but BEFORE the parent's own iteration checkpoint / goal completion.
    The backend records the crash as a state error (it never propagates), so the
    parent task is left orphaned. We force it back to ``running`` + result NULL to
    model the on-disk state a killed process leaves.

  ACT 2 — RECOVER
    ``recover_durable_tasks(pool, backend2)`` resumes the ROOT parent (children are
    NOT listed — they are resumed transitively when the parent re-delegates). The
    recovering parent provider re-dispatches ``delegate_task`` at the SAME
    iteration → the SAME child_task_id is re-derived (deterministic from
    parent_task_id + iteration + args). Because ``delegate_task`` is itself a
    ``write``-severity side effect, it is ledger-guarded under the PARENT's task;
    its row COMMITTED in ACT 1 (the crash fell after the dispatch returned + the
    guard committed), so the recovery re-dispatch hits ``already_committed`` and the
    delegate body — and therefore the whole child sub-pipeline + its write W — is
    NOT re-executed. The parent completes with the final answer.

    NOTE — the realized exactly-once mechanism. The write W is protected at TWO
    ledger layers: the parent's ``delegate_task`` row (which fires here, making the
    re-delegation a no-op) AND the child's own ``file_report`` row (defense-in-depth
    — it would short-circuit W to ``already_committed`` if the sub-pipeline ever did
    re-run). Either way the GUARANTEE proven is the same: the delegated child's
    write W runs EXACTLY ONCE across the crash. The parent-level short-circuit is
    the stronger of the two and is what fires in this architecture, because
    ledger_guard commits the delegate atomically on return (no provider-controlled
    window between the child W commit and the delegate commit) and child failures
    are isolated by the delegator — so the "delegate intent without commit" window
    is not reachable through the provider seam.

  ASSERT (the capstone — D1 §1 guarantee)
    * ``write.runs == 1`` — the delegated child's write W ran EXACTLY ONCE across
      crash + recovery.
    * exactly ONE committed ``file_report`` ledger row (under the child_task_id).
    * exactly ONE committed ``delegate_task`` ledger row (under the root parent) —
      the already_committed verdict that made re-delegation a no-op.
    * the child tasks row exists with ``parent_task_id`` set (durable child).
    * the ROOT parent ends ``completed`` with the final answer delivered.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.messaging.a2a import A2AQueue
from stackowl.owls.a2a_delegation import A2ADelegator
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.durable.delegation_link import derive_child_task_id
from stackowl.pipeline.durable.ledger import idempotency_key
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

_PARENT_GOAL = "Have the specialist file the report"
_SPECIALIST = "filer"
_SUB_GOAL = "file the quarterly report"
_CHILD_FINAL = "The quarterly report has been filed and confirmed by the specialist."
_FINAL = "Filed — confirmed by the specialist."


class _Crash(RuntimeError):
    """Simulated process kill: raised AFTER the child's write committed."""


class _WriteW(Tool):
    """A transactional write tool with a cross-crash run counter (the exactly-once proof).

    The SAME instance is shared across ACT 1 (crash) and ACT 2 (recovery), so
    ``runs`` is the cross-crash execution count — the D1 capstone proof.
    """

    def __init__(self) -> None:
        self.runs = 0

    @property
    def name(self) -> str:
        return "file_report"

    @property
    def description(self) -> str:
        return "file the report (transactional write)"

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
            commit_coupling="transactional",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.runs += 1
        return ToolResult(success=True, output="FILED", error=None, duration_ms=1.0)


# --------------------------------------------------------------------------- providers
#
# The provider registry keys on owl name: the PARENT provider serves "secretary"
# (the delegating root) and the CHILD provider serves the specialist owl. The
# execute step resolves the provider via registry.get(state.owl_name), so a child
# sub-pipeline (owl_name=_SPECIALIST) gets the CHILD provider and the root gets the
# PARENT provider — exactly the routing the plan note describes.


#: The parent delegates at iteration 1 (iteration 0 is a no-op think that
#: persists the pre-crash checkpoint). The child_task_id is derived from the
#: parent's iteration at delegate time, so ACT 1 and the ACT 2 resume (which
#: re-enters at checkpoint.iteration + 1 = 1) both delegate at iteration 1 → the
#: SAME child id.
_PARENT_DELEGATE_ITERATION = 1


class _CrashingParentProvider:
    """ACT 1 parent provider: checkpoint iter 0, delegate in iter 1, then CRASH.

    Iteration 0 does no side effect and completes (so a checkpoint at iteration 0
    is persisted — the durable cursor recovery resumes from). Iteration 1
    dispatches ``delegate_task``: the child runs its durable sub-pipeline,
    commits the write W under the child_task_id, the parent terminalizes the child
    ``completed`` — and THEN the parent provider RAISES. The crash falls AFTER the
    child write committed but BEFORE the parent's iteration-1 checkpoint /
    completion: a committed child effect with no parent checkpoint past it (the
    real D1 crash window).
    """

    def __init__(self) -> None:
        self.delegated = False

    @property
    def name(self) -> str:
        return "scripted-crashing-parent"

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
                        {"role": "assistant", "content": "I will have the specialist file it"},
                    ],
                    tool_call_records=[],
                )
            )
        # ITERATION 1 — delegate the write to the specialist. The durable
        # ctx.iteration is now 1, so the child id derives from iteration 1.
        await tool_dispatcher("delegate_task", {"goal": _SUB_GOAL, "to_owl": _SPECIALIST})
        self.delegated = True
        # CRASH: killed AFTER the child's write committed but BEFORE the parent's
        # iteration-1 checkpoint / goal completion (the real D1 crash window).
        raise _Crash("simulated parent kill after child write W committed")

    async def complete(self, *a: object, **k: object) -> CompletionResult:
        return CompletionResult(
            content="secretary", input_tokens=1, output_tokens=1, model="",
            provider_name=self.name, duration_ms=0.0,
        )

    async def stream(self, *a: object, **k: object) -> AsyncIterator[str]:  # pragma: no cover
        if False:
            yield ""


class _RecoveringParentProvider:
    """ACT 2 parent provider: re-delegate (child write replays), then finish.

    The recovery resumes the ROOT parent. This provider re-dispatches the SAME
    ``delegate_task`` call — the SAME child_task_id is re-derived, the child
    re-runs its sub-pipeline, and ``file_report`` REPLAYS (already_committed)
    rather than running a second time — then returns the final answer.
    """

    def __init__(self) -> None:
        self.saw_resume: int | None = None
        self.delegated = False
        self.delegate_result: str | None = None

    @property
    def name(self) -> str:
        return "scripted-recovering-parent"

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
        self.saw_resume = len(resume_messages) if resume_messages is not None else None
        # Re-delegate — re-derives the SAME child_task_id; the child's write W must
        # REPLAY (already_committed), not run a second time.
        self.delegate_result = await tool_dispatcher(
            "delegate_task", {"goal": _SUB_GOAL, "to_owl": _SPECIALIST}
        )
        self.delegated = True
        records = [
            {"name": "delegate_task", "args": {"goal": _SUB_GOAL, "to_owl": _SPECIALIST},
             "result": self.delegate_result}
        ]
        if on_iteration_complete is not None:
            await on_iteration_complete(
                ReActIterationState(
                    iteration=_PARENT_DELEGATE_ITERATION,
                    messages=[{"role": "assistant", "content": "specialist confirmed"}],
                    tool_call_records=records,
                )
            )
        return _FINAL, records


class _ChildProvider:
    """The specialist's provider: dispatch the transactional write W, then finish.

    On ACT 1 the write COMMITS under the child_task_id. On ACT 2 the recovery
    re-delegation is short-circuited at the PARENT's delegate_task ledger row
    (already_committed), so this child provider is NOT re-invoked at all — and were
    it ever re-invoked, the child's own file_report ledger row would replay W to
    already_committed (defense-in-depth). Either path keeps ``_WriteW`` at one run.
    The final answer is substantive so the parent's D3 relevance gate keeps it ``ok``.
    """

    def __init__(self) -> None:
        self.dispatch_count = 0

    @property
    def name(self) -> str:
        return "scripted-child"

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
        # The child's durable ctx is at iteration 0 on EVERY delegation (a fresh
        # backend.run, no durable_resume_*), so the write W lands at the SAME ledger
        # coordinate each time — the second delegation replays already_committed.
        self.dispatch_count += 1
        result = await tool_dispatcher("file_report", {})
        records = [{"name": "file_report", "args": {}, "result": result}]
        if on_iteration_complete is not None:
            await on_iteration_complete(
                ReActIterationState(
                    iteration=0,
                    messages=[{"role": "assistant", "content": "filed"}],
                    tool_call_records=records,
                )
            )
        return _CHILD_FINAL, records

    async def complete(self, *a: object, **k: object) -> CompletionResult:
        return CompletionResult(
            content=_SPECIALIST, input_tokens=1, output_tokens=1, model="",
            provider_name=self.name, duration_ms=0.0,
        )

    async def stream(self, *a: object, **k: object) -> AsyncIterator[str]:  # pragma: no cover
        if False:
            yield ""


class _RoutingProviderRegistry:
    """Provider registry keyed on owl name: parent for "secretary", child for the specialist.

    ``get(owl_name)`` is the execute step's first (most specific) provider lookup,
    so this routes each owl's turn to its scripted provider. ``get_with_cascade``
    is consulted by delegate_task's D3 relevance judge — returning None keeps the
    LLM judge stage skipped (fail-open) so the child's substantive answer stays ok.
    """

    def __init__(self, *, parent: object, child: object) -> None:
        self._parent = parent
        self._child = child

    def get(self, name: str) -> object:
        if name == _SPECIALIST:
            return self._child
        if name == "secretary":
            return self._parent
        from stackowl.exceptions import ProviderNotFoundError

        raise ProviderNotFoundError(name)

    def get_by_tier(self, tier: str) -> object:
        return self._parent

    def get_with_cascade(self, tier: str) -> object:
        # No fast provider for the relevance judge → judge_relevance fails open
        # (the structural pre-filter still runs); the child's answer stays ok.
        from stackowl.exceptions import AllProvidersUnavailableError

        raise AllProvidersUnavailableError("no fast provider in this journey")


def _specialist_manifest() -> OwlAgentManifest:
    """A write-capable specialist the parent can delegate to (bounds=None → all tools)."""
    return OwlAgentManifest(
        name=_SPECIALIST,
        role="report-filer",
        system_prompt="You file reports.",
        model_tier="powerful",
        tools=["file_report"],
    )


def _services(pool: DbPool, parent: object, child: object, write: _WriteW) -> StepServices:
    """REAL services: ToolRegistry(delegate_task + file_report), routing registry, a2a delegator."""
    from stackowl.tools.agents.delegate_task import DelegateTaskTool

    reg = ToolRegistry()
    reg.register(DelegateTaskTool())
    reg.register(write)

    owl_registry = OwlRegistry.with_default_secretary()
    owl_registry.register(_specialist_manifest())

    services = StepServices(
        provider_registry=_RoutingProviderRegistry(parent=parent, child=child),  # type: ignore[arg-type]
        tool_registry=reg,
        stream_registry=StreamRegistry(),
        owl_registry=owl_registry,
        a2a_queue=A2AQueue(),
        db_pool=pool,
    )
    # The delegate_task tool reads the delegator off services at execute time; the
    # delegator builds the child sub-pipeline from this SAME services instance, so
    # the child inherits the real registry/db_pool/providers (durable child path).
    services.a2a_delegator = A2ADelegator(services.a2a_queue, services)  # type: ignore[arg-type]
    return services


def _backend(services: StepServices) -> AsyncioBackend:
    return AsyncioBackend(services=services)


def _root_state() -> PipelineState:
    return PipelineState(
        trace_id="durable-deleg",
        session_id="durable-deleg",
        input_text=_PARENT_GOAL,
        channel="cli",
        owl_name="secretary",
        pipeline_step="",
        interactive=False,
    )


def _expected_child_id(parent_task_id: str) -> str:
    """The deterministic child_task_id for the parent's delegate_task at iteration 1."""
    canonical = {"goal": _SUB_GOAL, "to_owl": _SPECIALIST, "role": None, "context": None}
    key = idempotency_key(parent_task_id, _PARENT_DELEGATE_ITERATION, "delegate_task", canonical)
    return derive_child_task_id(key)


async def _roots(pool: DbPool) -> list[dict[str, Any]]:
    return await pool.fetch_all(
        "SELECT task_id, status, result, checkpoint_blob FROM tasks "
        "WHERE parent_task_id IS NULL",
        (),
    )


async def _children(pool: DbPool) -> list[dict[str, Any]]:
    return await pool.fetch_all(
        "SELECT task_id, parent_task_id, status FROM tasks WHERE parent_task_id IS NOT NULL",
        (),
    )


async def _file_report_ledger(pool: DbPool) -> list[dict[str, Any]]:
    return await pool.fetch_all(
        "SELECT status, tool_name, task_id FROM side_effect_ledger WHERE tool_name = 'file_report'",
        (),
    )


async def _delegate_ledger(pool: DbPool) -> list[dict[str, Any]]:
    return await pool.fetch_all(
        "SELECT status, tool_name, task_id FROM side_effect_ledger WHERE tool_name = 'delegate_task'",
        (),
    )


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "deleg.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    """The durable drive asserts not-test-mode; this journey drives the REAL path."""
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


async def test_durable_delegated_child_write_exactly_once_across_crash(pool: DbPool) -> None:
    # SHARED across both acts — the cross-crash execution-count proof (the capstone).
    write = _WriteW()

    # ---- ACT 1 — CRASH after the child's write W committed -------------------
    crashing_parent = _CrashingParentProvider()
    child_provider = _ChildProvider()
    services1 = _services(pool, crashing_parent, child_provider, write)
    backend1 = _backend(services1)
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    runner = DurableTaskRunner(store, backend1)

    final_state, parent_task_id = await runner.run(goal=_PARENT_GOAL, state=_root_state())

    # The parent provider crashed AFTER delegating (the child write committed).
    assert crashing_parent.delegated, "ACT 1 precondition: the parent must have delegated"
    assert child_provider.dispatch_count == 1, (
        f"ACT 1 precondition: the child sub-pipeline ran once, got {child_provider.dispatch_count}"
    )
    assert final_state.errors, "ACT 1 precondition: the crash must surface as a state error"
    assert write.runs == 1, f"ACT 1 precondition: write W ran {write.runs}x (want 1)"

    # The child ran DURABLY under its OWN child_task_id, with the write committed
    # in the child's ledger.
    expected_child = _expected_child_id(parent_task_id)
    children = await _children(pool)
    assert len(children) == 1, f"ACT 1: expected exactly one durable child row. {children}"
    assert children[0]["task_id"] == expected_child, (
        f"ACT 1: child id mismatch — {children[0]['task_id']!r} != {expected_child!r}"
    )
    assert children[0]["parent_task_id"] == parent_task_id, (
        "ACT 1: the durable child must link to the parent (parent_task_id set)"
    )
    ledger = await _file_report_ledger(pool)
    assert len(ledger) == 1 and ledger[0]["status"] == "committed", (
        f"ACT 1: the child write must be COMMITTED in the ledger. {ledger}"
    )
    assert ledger[0]["task_id"] == expected_child, (
        f"ACT 1: the write must be ledgered under the CHILD task_id. {ledger}"
    )

    # Model the on-disk state a KILLED parent leaves: the ROOT row orphaned in
    # 'running' with result NULL (a real kill cannot finalize). Null the result so
    # the post-recovery assertion genuinely proves RECOVERY wrote the final answer.
    await pool.execute(
        "UPDATE tasks SET status = 'running', result = NULL WHERE task_id = ?",
        (parent_task_id,),
    )
    pre_roots = await _roots(pool)
    assert pre_roots[0]["result"] is None, "ACT 1->2 precondition: parent result must be NULL"

    # ---- ACT 2 — RECOVER -----------------------------------------------------
    recovering_parent = _RecoveringParentProvider()
    recovering_child = _ChildProvider()
    services2 = _services(pool, recovering_parent, recovering_child, write)  # SAME write instance
    backend2 = _backend(services2)

    recoverer = await recover_durable_tasks(pool, backend2)
    # Only the ROOT parent is resumed (children resume transitively on re-delegation).
    assert recoverer.launched == 1, (
        f"recovery should launch exactly one (root) drive, got {recoverer.launched}"
    )
    await recoverer.drain()
    assert recoverer.in_flight == 0, "all background drives should have drained"

    # The resume genuinely continued the parent mid-transcript (B1/B2 resume_* forwarded).
    assert recovering_parent.saw_resume is not None and recovering_parent.saw_resume > 0, (
        "recovery did not forward the parent's checkpoint transcript into the provider"
    )
    # The recovered parent re-issued the SAME delegate_task call at the SAME
    # iteration (1) — re-deriving the SAME child_task_id.
    assert recovering_parent.delegated, "ACT 2: the recovered parent must re-delegate"

    # ---- THE EXACTLY-ONCE MECHANISM (the load-bearing D1 invariant) ----------
    # On recovery the parent re-enters iteration 1 and re-dispatches delegate_task.
    # `delegate_task` is itself a `write`-severity side effect, so it is ledger-
    # guarded under the PARENT's task at step_index 1. Its ledger row COMMITTED in
    # ACT 1 (the crash fell AFTER the dispatch returned + the guard committed), so
    # the recovery re-dispatch hits ``already_committed`` and the delegate_task body
    # — and therefore the WHOLE child sub-pipeline — is NOT re-executed. The child's
    # write W replays from the parent-level ledger short-circuit, so the child
    # provider is never re-invoked (its own file_report ledger row would ALSO
    # short-circuit W as defense-in-depth if the sub-pipeline ever did re-run).
    #
    # Either way the GUARANTEE is the same and is what this journey proves: the
    # delegated child's write W runs EXACTLY ONCE across crash + recovery.
    assert recovering_child.dispatch_count == 0, (
        "ACT 2: the recovery re-delegation must NOT re-execute the child sub-pipeline "
        "— the parent's delegate_task ledger row already_committed short-circuits it; "
        f"the child provider was re-invoked {recovering_child.dispatch_count}x"
    )

    # ---- THE CAPSTONE — exactly-once across crash + recovery -----------------
    assert write.runs == 1, (
        f"D1 CAPSTONE FAIL: exactly-once violated — the delegated child's write W ran "
        f"{write.runs}x across crash + recovery (it must run exactly once)."
    )
    ledger_after = await _file_report_ledger(pool)
    assert len(ledger_after) == 1, (
        f"D1 CAPSTONE FAIL: expected exactly ONE committed file_report ledger row. {ledger_after}"
    )
    assert ledger_after[0]["status"] == "committed"
    assert ledger_after[0]["task_id"] == expected_child, (
        "the single committed write must remain under the original child_task_id"
    )

    # The recovery re-delegation re-derived the SAME child id — exactly one child
    # row (create_child_task is ON CONFLICT no-op), never a detached duplicate.
    children_after = await _children(pool)
    assert len(children_after) == 1 and children_after[0]["task_id"] == expected_child, (
        f"the re-delegation must re-attach the SAME child, not spawn a new one. {children_after}"
    )

    # The parent's delegate_task is itself ledgered exactly once (under the ROOT
    # parent task) — the row whose already_committed verdict made the recovery
    # re-delegation a no-op. Exactly one committed row, under the parent (NOT child).
    deleg_ledger = await _delegate_ledger(pool)
    assert len(deleg_ledger) == 1 and deleg_ledger[0]["status"] == "committed", (
        f"the parent delegate_task must be committed exactly once. {deleg_ledger}"
    )
    assert deleg_ledger[0]["task_id"] == parent_task_id, (
        "the delegate_task effect must be ledgered under the PARENT (root) task"
    )

    # ---- The ROOT parent goal completes + delivers the final answer ----------
    roots_after = await _roots(pool)
    assert len(roots_after) == 1
    assert roots_after[0]["status"] == "completed", (
        f"D1 FAIL: the recovered root parent did not complete. status={roots_after[0]['status']!r}"
    )
    assert roots_after[0]["result"] == _FINAL, (
        f"D1 FAIL: the parent final answer was not delivered. result={roots_after[0]['result']!r}"
    )


async def test_child_layer_exactly_once_when_parent_short_circuit_removed(pool: DbPool) -> None:
    """CHILD-LAYER exactly-once — force the child sub-pipeline to RE-RUN, prove W replays.

    The capstone test above proves exactly-once via the PARENT'S ``delegate_task``
    ledger row: on recovery that row replays ``already_committed`` so the child
    sub-pipeline is never re-entered (``recovering_child.dispatch_count == 0``). That
    hides the CHILD layer — the child's OWN durable sub-task + its OWN side-effect
    ledger — which is the defense-in-depth that closes the spec §6 window (a crash
    AFTER the child W commits but BEFORE the parent's delegate_task commits).

    This test exercises that hidden layer directly. Between ACT 1 (parent+child ran,
    child W committed, crash) and ACT 2 (recovery) we DELETE the parent's
    ``delegate_task`` ledger row. That removes the parent-level short-circuit, so on
    recovery the parent's delegate_task RE-RUNS → the child sub-pipeline IS re-entered
    (``recovering_child.dispatch_count >= 1``) → the child re-dispatches ``file_report``
    → and the CHILD'S OWN ledger returns ``already_committed`` for W. W does NOT
    physically re-execute (``write.runs == 1``). That is the child-layer exactly-once
    proof.
    """
    # SHARED across both acts — the cross-crash execution-count proof.
    write = _WriteW()

    # ---- ACT 1 — CRASH after the child's write W committed -------------------
    crashing_parent = _CrashingParentProvider()
    child_provider = _ChildProvider()
    services1 = _services(pool, crashing_parent, child_provider, write)
    backend1 = _backend(services1)
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    runner = DurableTaskRunner(store, backend1)

    final_state, parent_task_id = await runner.run(goal=_PARENT_GOAL, state=_root_state())

    assert crashing_parent.delegated, "ACT 1 precondition: the parent must have delegated"
    assert child_provider.dispatch_count == 1, (
        f"ACT 1 precondition: the child sub-pipeline ran once, got {child_provider.dispatch_count}"
    )
    assert final_state.errors, "ACT 1 precondition: the crash must surface as a state error"
    assert write.runs == 1, f"ACT 1 precondition: write W ran {write.runs}x (want 1)"

    expected_child = _expected_child_id(parent_task_id)
    children = await _children(pool)
    assert len(children) == 1 and children[0]["task_id"] == expected_child, (
        f"ACT 1: expected exactly one durable child under the derived id. {children}"
    )
    ledger = await _file_report_ledger(pool)
    assert len(ledger) == 1 and ledger[0]["status"] == "committed", (
        f"ACT 1: the child write must be COMMITTED in the ledger. {ledger}"
    )
    assert ledger[0]["task_id"] == expected_child

    # Sanity: ACT 1 DID commit the parent's delegate_task ledger row (the row we
    # are about to delete to remove the parent-level short-circuit).
    deleg_pre = await _delegate_ledger(pool)
    assert len(deleg_pre) == 1 and deleg_pre[0]["status"] == "committed", (
        f"ACT 1: the parent delegate_task row must be committed before we delete it. {deleg_pre}"
    )
    assert deleg_pre[0]["task_id"] == parent_task_id

    # Model the on-disk state a KILLED parent leaves (orphaned 'running', result NULL).
    await pool.execute(
        "UPDATE tasks SET status = 'running', result = NULL WHERE task_id = ?",
        (parent_task_id,),
    )

    # ---- THE SEAM — delete the parent's delegate_task ledger row -------------
    # Removing the PARENT-layer short-circuit forces the recovery re-delegation to
    # actually re-run the child sub-pipeline, exercising the CHILD'S OWN ledger.
    await pool.execute(
        "DELETE FROM side_effect_ledger WHERE task_id = ? AND tool_name = 'delegate_task'",
        (parent_task_id,),
    )
    deleg_gone = await _delegate_ledger(pool)
    assert deleg_gone == [], (
        f"the parent delegate_task short-circuit row must be deleted. {deleg_gone}"
    )
    # The child's file_report ledger row MUST survive — it is the child-layer guard.
    ledger_still = await _file_report_ledger(pool)
    assert len(ledger_still) == 1 and ledger_still[0]["status"] == "committed", (
        f"the child file_report ledger row must remain (the child-layer guard). {ledger_still}"
    )

    # ---- ACT 2 — RECOVER (child sub-pipeline RE-RUNS this time) ---------------
    recovering_parent = _RecoveringParentProvider()
    recovering_child = _ChildProvider()
    services2 = _services(pool, recovering_parent, recovering_child, write)  # SAME write instance
    backend2 = _backend(services2)

    recoverer = await recover_durable_tasks(pool, backend2)
    assert recoverer.launched == 1, (
        f"recovery should launch exactly one (root) drive, got {recoverer.launched}"
    )
    await recoverer.drain()
    assert recoverer.in_flight == 0, "all background drives should have drained"

    assert recovering_parent.delegated, "ACT 2: the recovered parent must re-delegate"

    # ---- THE CHILD-LAYER PROOF (load-bearing) --------------------------------
    # With the parent short-circuit gone, the re-delegation MUST re-enter the child
    # sub-pipeline this time.
    assert recovering_child.dispatch_count >= 1, (
        "ACT 2: deleting the parent delegate_task ledger row must force the child "
        "sub-pipeline to RE-RUN (the parent-level short-circuit is gone); "
        f"the child provider was re-invoked {recovering_child.dispatch_count}x"
    )
    # ...yet the write W did NOT physically re-execute — the CHILD'S OWN file_report
    # ledger row replayed it as already_committed. THIS is the child-layer
    # exactly-once proof. (write.runs == 2 here would be a real D1 child-layer bug.)
    assert write.runs == 1, (
        f"D1 CHILD-LAYER FAIL: exactly-once violated — the child sub-pipeline re-ran "
        f"({recovering_child.dispatch_count}x) and the write W physically executed "
        f"{write.runs}x. The child's own ledger must have replayed W as already_committed."
    )

    # Exactly ONE committed file_report ledger row, still under the child_task_id
    # (the re-run did not append a second row).
    ledger_after = await _file_report_ledger(pool)
    assert len(ledger_after) == 1, (
        f"D1 CHILD-LAYER FAIL: expected exactly ONE committed file_report ledger row "
        f"under the child_task_id (the re-run replayed, did not re-commit). {ledger_after}"
    )
    assert ledger_after[0]["status"] == "committed"
    assert ledger_after[0]["task_id"] == expected_child

    # Still exactly one durable child row (re-delegation re-attaches the SAME child).
    children_after = await _children(pool)
    assert len(children_after) == 1 and children_after[0]["task_id"] == expected_child, (
        f"the re-delegation must re-attach the SAME child, not spawn a new one. {children_after}"
    )

    # ---- The ROOT parent goal still completes + delivers the final answer ----
    roots_after = await _roots(pool)
    assert len(roots_after) == 1
    assert roots_after[0]["status"] == "completed", (
        f"D1 FAIL: the recovered root parent did not complete. status={roots_after[0]['status']!r}"
    )
    assert roots_after[0]["result"] == _FINAL, (
        f"D1 FAIL: the parent final answer was not delivered. result={roots_after[0]['result']!r}"
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
