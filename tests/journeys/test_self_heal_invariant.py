"""Self-heal W2.T11 — the RESPONSES-ONLY-INVARIANT merge gate (end-to-end).

THE invariant this gate proves through the REAL gateway/pipeline: a hard provider
failure mid-turn must produce a NON-EMPTY honest message to the user AND leave the
failure RECORDED — it must NEVER flip a real failure into a fake success. The floor
(``synthesize_floor``, wired into ``execute.py`` at T10) only ever ADDS to
``responses``; it NEVER clears ``errors``. Three consumers infer success from
error-absence (durable status map → ``failed``, A2A status, parliament), so an
honest message that also cleared the error would be a silent failed→completed flip.

Two tests, both driving the REAL pipeline with ONLY the AI provider mocked
(mirroring ``tests/journeys/test_self_heal_lying_judge.py`` and
``tests/journeys/test_j_durable_goal.py``):

* **Test 1 (gateway hard-exception)** — a scripted provider whose
  ``complete_with_tools`` RAISES mid-turn, driven through the REAL
  ``GatewayScanner`` → ``AsyncioBackend`` → execute step. With NO healthy fallback
  provider (the same failing provider answers every cascade tier, so the
  ``critical_failure`` localized-apology cascade ALSO fails) the deterministic floor
  is the backstop. Asserts: the user-visible response is NON-EMPTY honest text AND
  ``state.errors`` is NON-EMPTY (the failure stays recorded — THE invariant) AND the
  delivered chunk is a floor chunk, not blank.

* **Test 2 (durable terminal status)** — the SAME hard failure driven through the
  REAL ``DurableTaskRunner`` over a real (migrated) ``DurableTaskStore``. Asserts the
  task's TERMINAL status is ``failed`` (the ``errors`` → failed mapping in
  ``DurableTaskRunner._drive``) AND a non-empty response was produced — proving the
  floor did NOT flip the durable task to ``completed``.

Test 3 (a delegated child that hard-fails returns ``A2AResult.status != ok``) is
DEFERRED to W4.T16 (the delegation-floor task): the delegation-floor surface is
W4's enforcement seam, and wiring a full parent→child delegated turn here would
carry more scaffolding than a focused responses-only gate should. The honest-failure
delegation result shapes already live in ``tools/agents/results.py`` (every
``_honest_failed_result`` sets ``success=False``); W4.T16 owns the end-to-end proof.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Generator
from typing import Any, Literal

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.durable.task_runner import DurableTaskRunner
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult
from stackowl.providers.react_callback import IterationCallback
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry

_PROVIDER_BLEW_UP = "provider exploded mid-loop (host unreachable)"


# --------------------------------------------------------------------------- #
# A minimal tool so the execute step takes the tool-loop path.
# --------------------------------------------------------------------------- #


class _NoopTool(Tool):
    @property
    def name(self) -> str:
        return "noop_tool"

    @property
    def description(self) -> str:
        return "A tool so the execute step takes the tool-loop path."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="RAN", duration_ms=1.0)


# --------------------------------------------------------------------------- #
# The ONLY mock: a scripted provider whose tool loop RAISES (the hard-exception
# path). Its `complete` ALSO fails, so the critical_failure apology cascade finds
# no healthy provider and the deterministic floor is the backstop (the simpler
# "NO healthy fallback → floor fires" case the gate targets).
# --------------------------------------------------------------------------- #


class _ExplodingProvider:
    """Stands in for the owl's LLM. complete_with_tools raises mid-loop; complete
    (the apology-cascade entry) also raises so no healthy fallback exists."""

    @property
    def name(self) -> str:
        return "exploding-fake"

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
        raise RuntimeError(_PROVIDER_BLEW_UP)

    async def complete(self, *a: object, **k: object) -> CompletionResult:
        # The apology cascade calls this — it ALSO fails (the provider is down), so
        # no healthy fallback exists and the floor (not the apology) is the backstop.
        raise RuntimeError(_PROVIDER_BLEW_UP)

    async def stream(self, *a: object, **k: object) -> AsyncIterator[str]:  # pragma: no cover
        if False:
            yield ""


class _FakeProviderRegistry:
    """Returns the same (exploding) provider for every tier/name — so the apology
    cascade also resolves a dead provider (no healthy fallback)."""

    def __init__(self, p: _ExplodingProvider) -> None:
        self._p = p

    def get(self, name: str) -> _ExplodingProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _ExplodingProvider:
        return self._p

    def get_with_cascade(self, tier: str) -> tuple[_ExplodingProvider, str]:
        """(provider, model), matching ProviderRegistry.get_with_cascade.

        RETURNED A BARE PROVIDER UNTIL 2026-08-21. Production unpacks it, so the
        apology cascade died on `TypeError: cannot unpack non-iterable
        _ExplodingProvider object` BEFORE ever reaching the exploding provider this
        test exists to exercise. The merge-gate assertion then failed for a reason
        that had nothing to do with the merge gate.
        """
        return (self._p, "test-model")


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def _backend(provider: _ExplodingProvider, pool: DbPool | None = None) -> AsyncioBackend:
    reg = ToolRegistry()
    reg.register(_NoopTool())
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=reg,
        stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
        db_pool=pool,
    )
    return AsyncioBackend(services=services)


@pytest.fixture(autouse=True)
def _live_io() -> Generator[None]:
    """Drive the REAL path (the durable runner asserts not-test-mode)."""
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Test 1 — gateway hard-exception: a non-empty honest message AND the failure stays
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_hard_exception_floors_message_and_keeps_failure() -> None:
    """A hard provider failure mid-turn, driven through the REAL gateway/pipeline,
    floors a NON-EMPTY honest message AND leaves the error recorded. THE invariant:
    responses-only — the floor adds a message but never clears errors."""
    provider = _ExplodingProvider()
    backend = _backend(provider)
    scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())

    msg = IngressMessage(
        text="please summarize that page for me",
        session_key="sess-self-heal-invariant",
        channel="cli",
        trace_id="trace-self-heal-invariant-1",
    )
    decision = scanner.scan(msg)
    input_text = (
        decision.stripped_text if decision.stripped_text is not None else msg.text
    )
    state = PipelineState(
        trace_id=msg.trace_id,
        session_key=msg.session_key,
        input_text=input_text,
        channel=msg.channel,
        owl_name=decision.target,
        pipeline_step="start",
        interactive=True,
    )

    final_state = await backend.run(state)
    delivered = "".join(c.content for c in final_state.responses)

    # INVARIANT (part 1) — the user gets a NON-EMPTY honest message.
    assert final_state.responses, (
        "MERGE-GATE FAIL: the hard provider failure produced NO response chunk — "
        "the user was left in silence (the never-empty floor did not fire)."
    )
    assert delivered.strip(), (
        "MERGE-GATE FAIL: the delivered response is empty — the never-empty floor "
        "invariant was violated."
    )

    # INVARIANT (part 2) — the failure STAYS recorded (responses-only): errors is
    # non-empty, so durable status / A2A status / parliament still see a FAILURE.
    assert final_state.errors, (
        "MERGE-GATE FAIL: the error was dropped — an honest message flipped a real "
        "failure into a fake success (responses-only invariant violated)."
    )
    assert any("execute:" in e for e in final_state.errors), (
        f"MERGE-GATE FAIL: the original execute error marker is gone. "
        f"errors={final_state.errors!r}"
    )

    # With NO healthy fallback the deterministic FLOOR (not the apology cascade) is
    # the backstop — assert the surviving response is a floor chunk (not blank).
    assert any(c.is_floor and c.content for c in final_state.responses), (
        "MERGE-GATE FAIL: no non-empty floor chunk survived — the zero-provider "
        "backstop did not produce the honest floor message."
    )


# --------------------------------------------------------------------------- #
# Test 2 — durable: a hard-failing task stays `failed` WITH a user message
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_durable_task_hard_fail_is_never_a_fake_success(tmp_db: DbPool) -> None:
    """A durable task whose provider hard-fails mid-turn must NEVER read as success,
    and the failure must be RECORDED so the next attempt is constrained.

    THE RULE CHANGED AND THIS GATE IS RE-EXPRESSED, NOT RELAXED. It used to assert
    ``status == "failed"``, which was the pre-ONE-LOOP way of saying "not a fake
    success". Since 2026-08-17 a failure is returned to the loop as ``pending`` *with
    what failed* — verified in `store.update_status`, which routes a non-terminal
    ``failed`` through `fail_and_requeue`: attempt_count incremented, last_error and
    last_failure_class recorded, banned_capabilities accumulated. Asserting the old
    literal here would now demand the platform re-acquire a defect it fixed after
    measuring 850 failed rows, not one of which had ever reached attempt_count > 0 —
    including the turn that left Bakir without an answer.

    WHAT THE GATE LOCKS IS UNCHANGED AND IS NOW STRICTER: the floor's responses-only
    write must not flip a real failure into a success, and the failure must survive
    ON THE ROW. The old assertion checked the first and not the second.

    REAL DurableTaskStore over a migrated DB; ONLY the AI provider is mocked."""
    provider = _ExplodingProvider()
    backend = _backend(provider, pool=tmp_db)
    store = DurableTaskStore(tmp_db)
    runner = DurableTaskRunner(store, backend)

    state = PipelineState(
        trace_id="trace-self-heal-durable-1",
        session_key="sess-self-heal-durable",
        input_text="please finish my task",
        channel="telegram",
        owl_name="secretary",
        pipeline_step="start",
        interactive=False,
    )

    final_state, task_id = await runner.run(goal="finish my task", state=state)

    task = await store.get(task_id)
    # (1) THE INVARIANT — never a fake success. This is what the gate is for.
    assert task.status != "completed", (
        f"MERGE-GATE FAIL: the durable task read as COMPLETED after a hard failure "
        f"— the floor flipped it to a fake success. status={task.status!r}"
    )
    # (2) The failure is RETURNED TO THE LOOP, not dropped on the floor. `pending`
    # is the ONE LOOP contract; pinned so a silent revert to terminal-on-first-
    # failure fails loudly here rather than re-creating the 850 unreachable rows.
    assert task.status == "pending", (
        f"expected the failure to be returned to the loop as `pending`, got "
        f"{task.status!r}"
    )
    # (3) …WITH WHAT FAILED. A retry that does not know why the last one died is
    # the "blind" retry the loop rule exists to prevent, and the old assertion
    # never checked this at all.
    assert task.attempt_count >= 1, (
        f"the failed attempt was not counted — attempt_count={task.attempt_count}"
    )
    assert (task.last_error or "").strip(), (
        "the failure was returned to the loop with NO recorded reason, so the next "
        "attempt is blind"
    )

    # AND a non-empty honest response was still produced (the user is not in silence).
    delivered = "".join(c.content for c in final_state.responses)
    assert delivered.strip(), (
        "MERGE-GATE FAIL: the durable hard-failure produced no user message — the "
        "never-empty floor did not fire on the durable path."
    )

    # The invariant, asserted at the state level too (the durable status MAP is the
    # `errors`-non-empty consumer T10's regression also pins).
    assert final_state.errors, (
        "MERGE-GATE FAIL: the durable final state dropped the error — responses-only "
        "invariant violated (a recorded failure became a fake success)."
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])


# --------------------------------------------------------------------------- #
# Test 3 — the retry is BOUNDED: a task that always fails is dead-lettered
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_permanently_failing_task_is_dead_lettered_not_retried_forever(
    tmp_db: DbPool,
) -> None:
    """THE LOCK THE OLD ASSERTION ACCIDENTALLY PROVIDED, now made deliberate.

    Test 2 used to assert `status == "failed"`, which — as a side effect of
    demanding a terminal value — also proved a failing task could not spin forever.
    Re-expressing that gate to the ONE LOOP contract (`pending`, with what failed)
    removes that coverage, and "returns to pending on every failure" with no ceiling
    is precisely the no-decay shape this programme keeps finding: a queue that only
    ever re-queues.

    It IS bounded — `fail_and_requeue` computes `exhausted = attempts >= ceiling`
    and dead-letters on `permanent or exhausted` — but that was true by reading, not
    by test. Reading is how the last four "it's intentional" conclusions started,
    and two of them were wrong.

    Note the terminal value is `dead_letter`, not `failed`: it also escalates ONCE,
    which is the honest trade recorded at the call site — "a few retries and one
    escalation, never a silent grind".
    """
    store = DurableTaskStore(tmp_db)
    task = DurableTask(
        task_id="bounded-retry-1",
        goal="a goal that can never succeed",
        status="pending",
        session_key="sess-bounded-retry",
        max_attempts=3,
    )
    await store.create(task)

    seen: list[str] = []
    # Generously more rounds than the ceiling: if the ceiling is ever removed this
    # loop still terminates, and the assertion below is what fails — not the suite.
    for _ in range(task.max_attempts + 5):
        status = await store.fail_and_requeue(
            task.task_id, error="the provider exploded again", failure_class="",
        )
        seen.append(status)
        if status == "dead_letter":
            break

    assert "dead_letter" in seen, (
        "UNBOUNDED RETRY: a task that fails every time never stopped. "
        f"max_attempts={task.max_attempts}, statuses seen={seen}"
    )
    assert len(seen) <= task.max_attempts, (
        f"the ceiling was not honoured: {len(seen)} attempts against "
        f"max_attempts={task.max_attempts}, statuses={seen}"
    )

    final = await store.get(task.task_id)
    assert final.status == "dead_letter"
    # It stopped WITH the reason on the row, not merely stopped.
    assert (final.last_error or "").strip(), (
        "dead-lettered with no recorded reason — the operator gets a dead row and "
        "no way to see why"
    )
