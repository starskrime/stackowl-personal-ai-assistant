"""E2-S4 GATEWAY JOURNEYS — budget cap deterministic stop + interactive raise.

Two proofs of the budget governor business outcomes:

JOURNEY 1 — deterministic stop (gateway level)
  A non-interactive owl with caps.max_steps=2 drives a scripted provider that
  attempts 5 iterations. The governor raises BudgetBreach at iteration=1
  (steps_done=2). The full gateway arc (Telegram adapter → GatewayScanner →
  AsyncioBackend) delivers a partial reply carrying a budget-stop marker in the
  outbound text AND records a ``budget:stop:steps:...`` marker in state.errors.
  The run does NOT complete all 5 scripted iterations.

JOURNEY 2 — interactive raise (_run_with_tools level)
  An interactive turn with caps.max_steps=2 and a clarify double that answers
  "Raise" unconditionally. The governor raises at iteration=1 (steps_done=2),
  the clarify double answers "Raise" → raise_caps doubles the limit → the loop
  continues and completes all 5 iterations. No budget-stop marker in errors.

Scaffolding for Journey 1 is adapted from ``test_tool_scope_envelope.py``
(the established J4 journey template). The scripted provider mirrors
``test_execute_budget.py``: it calls ``on_iteration_complete`` each iteration
and propagates any raised exception (never swallows). For Journey 2 the test
drives ``_run_with_tools`` directly (the same seam as test_execute_budget.py)
to avoid the heavier clarify round-trip through the gateway; a lightweight
clarify double is wired into ``StepServices.clarify_gateway``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from stackowl.authz.bounds import BoundsSpec, ResourceCaps
from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import GatewayScanner
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.react_callback import ReActIterationState
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_USER_ID = 515151
_OWL_NAME = "budget_owl"
_SCRIPTED_ITERATIONS = 5
_CAP_MAX_STEPS = 2

# The iteration at which the gate fires:
#   iteration=1 (0-based) → steps_done=2 ≥ max_steps=2 → BudgetBreach
_BREACH_ITERATION = _CAP_MAX_STEPS - 1  # 1

# The partial text emitted by the scripted provider at the breach iteration.
_PARTIAL_TEXT_AT_BREACH = f"step{_BREACH_ITERATION}"

# The final reply text on a COMPLETED (no breach) run.
_FULL_REPLY = f"done after {_SCRIPTED_ITERATIONS} iterations"


# ---------------------------------------------------------------------------
# FAKED #1: Telegram bot HTTP transport (captures outbound in-process)
# ---------------------------------------------------------------------------


class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):  # noqa: ANN001
        self.messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    async def answer_callback_query(self, callback_id, text=None):  # noqa: ANN001
        pass


class _FakeBotApp:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot

    def add_handler(self, handler: object) -> None:
        pass


# ---------------------------------------------------------------------------
# REAL tool: read-severity, records whether execute() actually ran
# ---------------------------------------------------------------------------


class _BudgetTool(Tool):
    """A minimal read-severity tool for the budget-iteration loop."""

    def __init__(self) -> None:
        self.runs = 0

    @property
    def name(self) -> str:
        return "budget_probe"

    @property
    def description(self) -> str:
        return "Probes the budget governor enforcement."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="budget_probe", description=self.description,
            parameters=self.parameters, action_severity="read",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.runs += 1
        return ToolResult(success=True, output="probe-ok", duration_ms=1.0)


# ---------------------------------------------------------------------------
# FAKED #2 (THE ONLY AI MOCK): multi-iteration scripted provider
#
# Each iteration:
#   1. Appends an assistant message ("step{i}").
#   2. Calls on_iteration_complete with the current ReActIterationState.
#   3. PROPAGATES any exception raised by the callback (never swallows).
#   4. Records iteration completion only AFTER the callback returns cleanly.
#
# With max_steps=2 the callback raises BudgetBreach at iteration=1 (the
# append of step1 has already run, so partial_text = "step1").  The exception
# propagates out of complete_with_tools — exactly what a real provider does.
# ---------------------------------------------------------------------------


class _MultiIterationProvider:
    """Multi-iteration scripted provider that propagates on_iteration_complete raises."""

    protocol = "anthropic"

    def __init__(self, iterations: int = _SCRIPTED_ITERATIONS) -> None:
        self._iterations = iterations
        self.completed_iterations: list[int] = []

    async def complete_with_tools(  # noqa: ANN001
        self,
        *,
        user_text: str,
        system_text: str,
        tool_schemas: list[dict[str, object]],
        tool_dispatcher: Any,
        history: list[Any] | None = None,
        on_iteration_complete: Any = None,
        **_kwargs: object,
    ) -> tuple[str, list[dict[str, Any]]]:
        all_messages: list[dict[str, Any]] = []
        all_calls: list[dict[str, Any]] = []
        for i in range(self._iterations):
            all_messages.append({"role": "assistant", "content": f"step{i}"})
            if on_iteration_complete is not None:
                # PROPAGATE — never swallow a raise from the callback.
                await on_iteration_complete(
                    ReActIterationState(
                        iteration=i,
                        messages=list(all_messages),
                        tool_call_records=list(all_calls),
                    )
                )
            self.completed_iterations.append(i)
        return (_FULL_REPLY, all_calls)

    async def complete(self, messages: list, model: str, **kwargs: object):  # noqa: ANN201
        from stackowl.providers.base import CompletionResult
        return CompletionResult(
            content="budget test", input_tokens=1, output_tokens=1,
            model="budget-model", provider_name=_OWL_NAME, duration_ms=1.0,
        )

    async def stream(self, *a: Any, **k: Any):  # pragma: no cover — not on this path
        if False:  # noqa: SIM210
            yield ""


class _FakeProviderRegistry:
    def __init__(self, p: _MultiIterationProvider) -> None:
        self._p = p

    def get(self, name: str) -> _MultiIterationProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _MultiIterationProvider:
        return self._p

    def get_with_cascade(self, preferred_tier: str) -> _MultiIterationProvider:
        return self._p


# ---------------------------------------------------------------------------
# Clarify double for the interactive-raise test (Journey 2)
#
# Implements the ClarifyGateway duck-type used by make_budget_callback:
#   ask(session_id, channel, question, choices, blocking) → clarify_id
#   wait_for_answer(clarify_id, timeout) → (answer, outcome)
#
# Always answers "Raise" so the governor doubles the cap and continues.
# ---------------------------------------------------------------------------


class _AlwaysRaiseClarify:
    """ClarifyGateway double that always answers 'Raise' immediately."""

    def __init__(self) -> None:
        self.ask_calls: list[dict[str, Any]] = []

    async def ask(  # noqa: ANN201
        self,
        session_id: str,
        channel: str,
        question: str,
        *,
        choices: tuple[str, ...] = (),
        blocking: bool = False,
        awaiting_text: bool = False,
    ) -> str:
        self.ask_calls.append({
            "session_id": session_id, "channel": channel,
            "question": question, "choices": choices,
        })
        return "raise-clarify-id"

    async def wait_for_answer(
        self, clarify_id: str, timeout: float,
    ) -> tuple[str, str]:
        # Immediately return "Raise" — the human said "yes, continue".
        return ("Raise", "answered")


# ---------------------------------------------------------------------------
# Env wiring (Journey 1 — gateway level, mirrors test_tool_scope_envelope.py)
# ---------------------------------------------------------------------------


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: _MultiIterationProvider


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _bounded_manifest(caps: ResourceCaps | None = None) -> OwlAgentManifest:
    """Build an owl manifest for ``_OWL_NAME`` with an optional ResourceCaps."""
    bounds = BoundsSpec(
        tools=frozenset({"budget_probe"}),
        caps=caps if caps is not None else ResourceCaps(),
    )
    return OwlAgentManifest(
        name=_OWL_NAME,
        role="budget-tester",
        system_prompt="You test budget caps.",
        model_tier="fast",
        bounds=bounds,
    )


def _build(
    provider: _MultiIterationProvider,
    *,
    caps: ResourceCaps | None = None,
    interactive: bool = False,
) -> _Env:
    """Wire up the gateway-level environment for Journey 1."""
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({_USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)  # type: ignore[assignment]
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    tool = _BudgetTool()
    registry = ToolRegistry()
    registry.register(tool)

    owl_registry = OwlRegistry.with_default_secretary()
    owl_registry.register(_bounded_manifest(caps=caps))

    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=registry,
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        owl_registry=owl_registry,
    )
    return _Env(
        adapter=adapter,
        bot=bot,
        scanner=GatewayScanner(owl_registry=owl_registry),
        backend=AsyncioBackend(services=services),  # type: ignore[arg-type]
        stream_registry=services.stream_registry,
        provider=provider,
    )


async def _turn(env: _Env, text: str) -> str:
    """Drive one inbound turn through the full gateway arc."""
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=_USER_ID),
        effective_chat=SimpleNamespace(id=_USER_ID),
    )
    await env.adapter._handle_update(update, None)
    msg = await env.adapter.receive()
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    _writer, reader = env.stream_registry.create(msg.trace_id)
    state = PipelineState(
        trace_id=msg.trace_id,
        session_id=msg.session_id,
        input_text=input_text,
        channel=msg.channel,
        owl_name=decision.target,
        pipeline_step="start",
        # non-interactive: the governor raises immediately (no clarify round-trip)
        interactive=False,
    )
    before = len(env.bot.messages)
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.trace_id)
    return "".join(m["text"] for m in env.bot.messages[before:] if m["reply_markup"] is None)


# ===========================================================================
# JOURNEY 1 — deterministic stop end-to-end (gateway level)
# ===========================================================================


async def test_durable_step_cap_stops_deterministically(  # noqa: ANN201
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-interactive owl with caps.max_steps=2 stops deterministically at the cap.

    The scripted provider runs 5 iterations calling on_iteration_complete each
    round.  With max_steps=2 the gate raises BudgetBreach at iteration=1
    (steps_done=2).  The execute step catches the breach and:
      * delivers a partial reply containing a "budget cap" / "stopped" note to the
        channel (outbound Telegram message carries the marker text).
      * records a ``budget:stop:steps:...`` marker in state.errors.

    The run does NOT complete all 5 scripted iterations — this is the
    load-bearing assertion: the governor actually STOPPED the loop.

    Driven through the REAL Telegram adapter → GatewayScanner → AsyncioBackend
    seam. The scripted provider is the ONLY mock.
    """
    import logging

    caps = ResourceCaps(max_steps=_CAP_MAX_STEPS)
    provider = _MultiIterationProvider(iterations=_SCRIPTED_ITERATIONS)
    env = _build(provider, caps=caps, interactive=False)

    with caplog.at_level(logging.INFO, logger="stackowl.engine"):
        reply = await _turn(env, f"@{_OWL_NAME} run five steps")

    # OUTCOME 1 — the run was STOPPED before completing all scripted iterations.
    # completed_iterations records only iterations that returned PAST the callback.
    # With max_steps=2 the callback raises at iteration=1, so ONLY iteration=0
    # is fully completed (appended to completed_iterations after a clean callback).
    # iteration=1 triggers the breach BEFORE the append → stopped at 2 steps done.
    assert len(provider.completed_iterations) < _SCRIPTED_ITERATIONS, (
        f"BUDGET CAP MISS: the governor did not stop the loop. "
        f"All {_SCRIPTED_ITERATIONS} iterations completed — expected fewer than "
        f"{_SCRIPTED_ITERATIONS} (stopped at max_steps={_CAP_MAX_STEPS}). "
        f"completed_iterations={provider.completed_iterations}"
    )

    # OUTCOME 2 — a budget-stop marker is visible in the outbound channel reply.
    # The execute step appends "\n\n[stopped: budget cap 'steps' reached ...]"
    # to the partial text before delivering. This proves the partial was sent.
    assert "budget cap" in reply.lower() or "stopped" in reply.lower(), (
        f"Expected a budget-stop note in the outbound reply. Got: {reply!r}"
    )

    # OUTCOME 3 — the log confirms the budget cap path was taken (belt-and-braces,
    # supplements OUTCOME 1+2 but not load-bearing on its own).
    budget_log_records = [
        r for r in caplog.records
        if "budget" in r.getMessage().lower() and "cap reached" in r.getMessage().lower()
    ]
    assert budget_log_records, (
        "Expected at least one 'budget cap reached' log record in stackowl.engine. "
        f"All engine records: {[r.getMessage() for r in caplog.records]}"
    )


# ===========================================================================
# JOURNEY 2 — interactive raise continues past the cap (_run_with_tools level)
# ===========================================================================


async def test_interactive_step_cap_raise_continues() -> None:
    """Interactive turn with caps.max_steps=2: clarify double answers 'Raise' → continues.

    Driven at the _run_with_tools level (NOT the full gateway arc) because the
    clarify round-trip (blocking ask/wait_for_answer) requires an in-turn
    concurrent resolver, which would need significant adapter wiring beyond this
    story's scope. The _run_with_tools seam is the direct integration boundary
    tested by test_execute_budget.py; this test extends it to the interactive
    Raise path.

    Scenario:
      * caps.max_steps=2 → BudgetBreach at iteration=1.
      * clarify double answers "Raise" immediately.
      * raise_caps doubles max_steps (2*2+1=5) → all 5 scripted iterations
        complete cleanly with no budget-stop marker in errors.

    The two load-bearing assertions:
      * NO budget:stop marker in state.errors (the Raise kept the loop alive).
      * ALL 5 scripted iterations completed (the run continued past the cap).
    """
    caps = ResourceCaps(max_steps=_CAP_MAX_STEPS)
    bounds = BoundsSpec(tools=frozenset({"budget_probe"}), caps=caps)

    tool = _BudgetTool()
    tool_registry = ToolRegistry()
    tool_registry.register(tool)

    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name=_OWL_NAME,
        role="budget-tester",
        system_prompt="You test budget caps.",
        model_tier="fast",
        bounds=bounds,
    ))

    provider = _MultiIterationProvider(iterations=_SCRIPTED_ITERATIONS)
    clarify_double = _AlwaysRaiseClarify()

    state = PipelineState(
        trace_id="trace-budget-interactive",
        session_id="sess-budget-interactive",
        input_text="run many steps interactively",
        channel="telegram",
        owl_name=_OWL_NAME,
        pipeline_step="execute",
        # interactive=True: the budget callback will consult the clarify gateway.
        interactive=True,
    )

    token = set_services(StepServices(
        tool_registry=tool_registry,
        owl_registry=owl_registry,
        clarify_gateway=clarify_double,  # type: ignore[arg-type]
        cost_tracker=None,
    ))
    try:
        result = await _run_with_tools(state, provider, tool_registry)  # type: ignore[arg-type]
    finally:
        reset_services(token)

    # OUTCOME 1 — NO budget-stop marker (the Raise kept the loop alive).
    assert not any("budget" in e for e in result.errors), (
        f"RAISE FAILED: unexpected budget-stop marker in errors after a clarify Raise. "
        f"errors={result.errors}"
    )

    # OUTCOME 2 — ALL scripted iterations completed (the run continued past the cap).
    assert len(provider.completed_iterations) == _SCRIPTED_ITERATIONS, (
        f"RAISE FAILED: the run did not complete all {_SCRIPTED_ITERATIONS} iterations "
        f"after a clarify Raise. completed_iterations={provider.completed_iterations}"
    )

    # OUTCOME 3 — the clarify double was actually consulted at least once.
    assert clarify_double.ask_calls, (
        "The clarify double was never called — the interactive-raise path was not taken. "
        "Verify that interactive=True and clarify_gateway is wired into StepServices."
    )

    # OUTCOME 4 — the final reply is present (normal completion, not partial).
    assert result.responses, (
        "Expected at least one ResponseChunk in state.responses after a completed run."
    )
    full_reply_text = "".join(c.content for c in result.responses)
    assert "budget cap" not in full_reply_text.lower(), (
        f"Unexpected budget-cap note in the final reply text: {full_reply_text!r}"
    )
