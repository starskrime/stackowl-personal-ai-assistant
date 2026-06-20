"""Conversational-bypass journey — end-to-end proof of lean conversational path.

FR4 — "hi" is lean: when the router classifies a turn as conversational the
      execute step sets ``tools_used=False`` and ``total_est_tokens < 4000``,
      verified via the ``[pipeline] execute: context budget`` log record on the
      ``stackowl.engine`` logger.

FR5 — plain-stream delivery: the answer provider streams a greeting (non-empty),
      the tool loop is NEVER entered (no ``tool_loop entry`` log), and the
      persistence nudge is NEVER fired (no ``persistence judge ruled give-up``
      log).

FR6 — standard-unchanged: a router returning ``standard`` for a task input
      causes ``tools_used=True`` (the tool loop IS entered), proving that
      standard turns keep the full path.

Boot mirrors ``tests/journeys/test_recovery_explainability_journey.py`` and
``tests/journeys/test_circuit_aware_routing_journey.py``:
  * Real ``AsyncioBackend`` pipeline.
  * ``_ScriptedProvider`` (a custom ``ModelProvider`` subclass, NOT ``OpenAIProvider``
    + fake client) for the answer provider — avoids ``response.usage`` guard issues
    and supports both ``complete()`` (router/judge calls) and ``stream()``
    (conversational answer delivery).
  * ``_ConversationalRouterProvider`` — always returns ``secretary\\nconversational``
    for routing calls, ``{"delivered": true, ...}`` for persistence-judge calls.
  * ``_StandardRouterProvider`` — returns ``secretary\\nstandard`` for routing calls,
    ``{"delivered": true, ...}`` for judge calls.
  * Both answer providers are registered under ``"secretary"`` so
    ``_select_tool_provider`` Step 0 resolves them directly (correct for testing
    the execute / classify path, not the provider-cascade path).

Harness note — ``"secretary"`` slot registered:
  Unlike ``test_circuit_aware_routing_journey.py`` (which deliberately omits the
  ``"secretary"`` slot to force tier-cascade), here we DO register the answer
  provider under ``"secretary"`` so ``_select_tool_provider`` Step 0 picks it up
  directly.  The router provider is registered at the ``"fast"`` tier so the
  ``SecretaryRouter`` (triage step) uses it for the routing LLM call (which sets
  ``intent_class``).  The execute step uses the ``"secretary"`` answer provider.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

# --------------------------------------------------------------------------- #
# _ScriptedProvider — directly implements ModelProvider with canned responses.
#
# Returns CompletionResult directly for complete() (router/judge) and stream()
# (conversational answer delivery), and overrides complete_with_tools() to be
# genuinely tool-capable (the base default now RAISES on a tool schema — F120,
# no silent degrade — so a standard turn entering the tool loop needs a real
# tool-capable double, mirroring production openai/anthropic/ollama providers).
# --------------------------------------------------------------------------- #


class _ScriptedProvider(ModelProvider):
    """Returns canned responses in sequence; directly implements ModelProvider."""

    def __init__(self, name: str, replies: list[str]) -> None:
        self._name = name
        self._replies = replies
        self._i = 0
        self.calls: list[list[Message]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        self.calls.append(list(messages))
        idx = min(self._i, len(self._replies) - 1)
        text = self._replies[idx]
        self._i += 1
        return CompletionResult(
            content=text,
            input_tokens=1,
            output_tokens=1,
            model="scripted-model",
            provider_name=self._name,
            duration_ms=1.0,
        )

    async def stream(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:  # type: ignore[override]
        self.calls.append(list(messages))
        idx = min(self._i, len(self._replies) - 1)
        text = self._replies[idx]
        self._i += 1
        yield text

    async def complete_with_tools(
        self, *args: object, **kwargs: object
    ) -> tuple[str, list[object]]:
        """Tool-CAPABLE: enter the loop, return the scripted final answer + NO tool
        calls so the agentic loop terminates cleanly on the first pass.

        Required because the base ``complete_with_tools`` (F120,
        providers/base.py) RAISES ``ToolUseUnsupportedError`` when handed a tool
        schema by a provider that does not override it — it refuses to silently
        degrade. A standard turn entering the tool loop with a registered tool
        therefore needs a genuinely tool-capable double (real openai/anthropic/
        ollama providers all override this). The empty tool-call list is what
        makes the loop dispatch nothing and finish with the direct answer.
        Signature-robust (``*args``/``**kwargs``) so a future param on the real
        contract cannot silently break the double.
        """
        # Capture the history messages handed to the provider on this call so
        # cross-turn journey tests can assert on what context was assembled.
        self.calls.append(list(kwargs.get("history") or []))  # type: ignore[arg-type]
        idx = min(self._i, len(self._replies) - 1)
        text = self._replies[idx]
        self._i += 1
        return text, []


# --------------------------------------------------------------------------- #
# Router providers — used by SecretaryRouter (triage step) via the fast tier.
# Returns the appropriate two-line routing reply depending on the desired class.
# On persistence-judge calls (message contains "AGENT DRAFT REPLY") returns
# {"delivered: true, ...} so the judge is fail-open (does not block the turn).
# --------------------------------------------------------------------------- #


class _ConversationalRouterProvider(ModelProvider):
    """Fast-tier router: classifies every routing call as conversational."""

    @property
    def name(self) -> str:
        return "conversational-router-fake"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        joined = "\n".join(m.content for m in messages)
        content = (
            '{"delivered": true, "reason": "looks complete"}'
            if "AGENT DRAFT REPLY" in joined
            else "secretary\nconversational"
        )
        return CompletionResult(
            content=content,
            input_tokens=1,
            output_tokens=1,
            model="conversational-router-fake",
            provider_name="conversational-router-fake",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        yield "secretary\nconversational"


class _StandardRouterProvider(ModelProvider):
    """Fast-tier router: classifies every routing call as standard."""

    @property
    def name(self) -> str:
        return "standard-router-fake"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        joined = "\n".join(m.content for m in messages)
        content = (
            '{"delivered": true, "reason": "looks complete"}'
            if "AGENT DRAFT REPLY" in joined
            else "secretary\nstandard"
        )
        return CompletionResult(
            content=content,
            input_tokens=1,
            output_tokens=1,
            model="standard-router-fake",
            provider_name="standard-router-fake",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        yield "secretary\nstandard"


# --------------------------------------------------------------------------- #
# Simple tool for the standard-path test (FR6).
# --------------------------------------------------------------------------- #


class _EchoTool(Tool):
    """A trivial read tool that echoes its input — exercises the tool loop."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return "echo_tool"

    @property
    def description(self) -> str:
        return "Echoes the given text back."

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {"text": {"type": "string"}},
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            capability_tag=None,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        return ToolResult(success=True, output=str(kwargs.get("text", "")), error=None, duration_ms=1.0)


# --------------------------------------------------------------------------- #
# Service / backend builders.
# --------------------------------------------------------------------------- #


def _build_services_conversational(
    answer_provider: _ScriptedProvider,
    owl_registry: OwlRegistry,
    tool_registry: ToolRegistry,
) -> StepServices:
    """Build StepServices with a conversational-routing fast-tier provider."""
    preg = ProviderRegistry()
    # "secretary" slot → answer provider (Step 0 in _select_tool_provider picks this).
    preg.register_mock("secretary", answer_provider, tier="powerful")
    preg.register_mock("powerful", answer_provider, tier="powerful")
    router = _ConversationalRouterProvider()
    preg.register_mock("router", router, tier="fast")
    preg.register_mock("local-judge", router, tier="local")
    return StepServices(
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
    )


def _build_services_standard(
    answer_provider: _ScriptedProvider,
    owl_registry: OwlRegistry,
    tool_registry: ToolRegistry,
) -> StepServices:
    """Build StepServices with a standard-routing fast-tier provider."""
    preg = ProviderRegistry()
    preg.register_mock("secretary", answer_provider, tier="powerful")
    preg.register_mock("powerful", answer_provider, tier="powerful")
    router = _StandardRouterProvider()
    preg.register_mock("router", router, tier="fast")
    preg.register_mock("local-judge", router, tier="local")
    return StepServices(
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
    )


async def _execute_turn(
    text: str,
    session: str,
    trace: str,
    backend: AsyncioBackend,
) -> tuple[str, PipelineState]:
    """Run a single turn through the backend; return (delivered_text, final_state)."""
    scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())
    msg = IngressMessage(
        text=text,
        session_id=session,
        channel="cli",
        trace_id=trace,
    )
    decision = scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    state = PipelineState(
        trace_id=msg.trace_id,
        session_id=msg.session_id,
        input_text=input_text,
        channel=msg.channel,
        owl_name=decision.target,
        pipeline_step="start",
        interactive=True,
    )
    final_state = await backend.run(state)
    delivered = "".join(c.content for c in final_state.responses)
    return delivered, final_state


# =========================================================================== #
# FR4 + FR5 — "hi" is lean: no tool loop, tiny prompt, greeting delivered.
# =========================================================================== #


@pytest.mark.asyncio
async def test_conversational_hi_is_lean_no_tool_loop(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR4/FR5: A "hi" turn classified as conversational must:
    - Emit a ``[pipeline] execute: context budget`` log with
      intent_class=="conversational", tools_used==False, total_est_tokens < 4000.
    - Deliver a non-empty greeting (the scripted reply).
    - NEVER enter the tool loop (no ``tool_loop entry`` log record).
    - NEVER fire the persistence nudge (no ``persistence judge ruled give-up`` log).
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    greeting = "Hello! How can I help?"
    answer_provider = _ScriptedProvider("answer-conv", [greeting])
    owl_registry = OwlRegistry.with_default_secretary()
    tool_registry = ToolRegistry()  # empty — no tools registered

    services = _build_services_conversational(answer_provider, owl_registry, tool_registry)
    backend = AsyncioBackend(services=services)

    with caplog.at_level(logging.INFO, logger="stackowl.engine"):
        delivered, final_state = await _execute_turn(
            "hi",
            "sess-conv-bypass-fr4fr5",
            "trace-conv-bypass-1",
            backend,
        )

    # ===================================================================
    # No-error guard — a floor-on-error reply must not silently pass.
    # ===================================================================
    assert not final_state.errors, f"unexpected errors: {final_state.errors}"

    # ===================================================================
    # FR5 OUTCOME 1 — the greeting was delivered (non-empty reply).
    # ===================================================================
    assert delivered.strip(), (
        f"FR5 FAIL: delivered text is empty — the conversational turn produced no reply. "
        f"Got: {delivered!r}"
    )
    assert greeting in delivered, (
        f"FR5 FAIL: expected greeting {greeting!r} in delivered text. Got: {delivered!r}"
    )

    # ===================================================================
    # FR4 OUTCOME — the context budget log record was emitted with the
    # correct field values.
    # ===================================================================
    budget_records = [
        r for r in caplog.records
        if "[pipeline] execute: context budget" in r.getMessage()
    ]
    assert budget_records, (
        f"FR4 FAIL: '[pipeline] execute: context budget' log record not found. "
        f"Records: {[r.getMessage() for r in caplog.records]}"
    )
    budget_rec = budget_records[0]
    fields: dict[str, Any] = getattr(budget_rec, "_fields", {})

    assert fields.get("intent_class") == "conversational", (
        f"FR4 FAIL: intent_class in budget log is not 'conversational'. "
        f"Got _fields: {fields!r}"
    )
    assert fields.get("tools_used") is False, (
        f"FR4 FAIL: tools_used in budget log is not False — tool loop was entered. "
        f"Got _fields: {fields!r}"
    )
    total_tokens = fields.get("total_est_tokens", 99999)
    # Lower bound: the secretary persona + base prompt are assembled before execute,
    # so total_est_tokens must be > 0 — a zero here means assemble is unwired/broken.
    # Upper bound: conversational turns must stay lean (< 4000 tokens).
    # Observed value with default secretary persona + base prompt: ~756 tokens.
    assert isinstance(total_tokens, int) and total_tokens > 0, (
        f"FR4 FAIL: total_est_tokens={total_tokens!r} is 0 — system_prompt was not "
        f"assembled (assemble step unwired or persona missing). Got _fields: {fields!r}"
    )
    assert total_tokens < 4000, (
        f"FR4 FAIL: total_est_tokens={total_tokens!r} is not < 4000 — prompt is bloated. "
        f"Got _fields: {fields!r}"
    )

    # ===================================================================
    # FR5 OUTCOME 2 — the tool loop was NEVER entered.
    # ===================================================================
    assert not any("tool_loop entry" in r.getMessage() for r in caplog.records), (
        f"FR5 FAIL: 'tool_loop entry' log record found — the tool loop was entered "
        f"for a conversational turn. Records: {[r.getMessage() for r in caplog.records]}"
    )

    # ===================================================================
    # FR5 OUTCOME 3 — the persistence nudge was NEVER fired.
    # ===================================================================
    assert not any(
        "persistence judge ruled give-up" in r.getMessage() for r in caplog.records
    ), (
        f"FR5 FAIL: 'persistence judge ruled give-up' log record found — nudge fired "
        f"on a conversational turn. Records: {[r.getMessage() for r in caplog.records]}"
    )


# =========================================================================== #
# FR6 — standard turns are unchanged: tool loop IS entered.
# =========================================================================== #


@pytest.mark.asyncio
async def test_standard_turn_enters_tool_loop(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR6: A turn classified as standard must enter the tool loop (tools_used==True
    in the budget log). This proves standard turns keep the full path unchanged.

    The scripted answer provider returns a direct final answer (no tool calls) so
    the loop terminates cleanly — but the ENTRY to the tool loop is still logged,
    confirming the loop was entered even when no tools were actually dispatched.
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    final_answer = "The capital of France is Paris."
    answer_provider = _ScriptedProvider("answer-std", [final_answer])
    owl_registry = OwlRegistry.with_default_secretary()

    # Register a real tool so tool_registry.all() is non-empty — required for
    # _use_tools to be True (execute.py: tool_registry is not None and tool_registry.all()).
    echo_tool = _EchoTool()
    tool_registry = ToolRegistry()
    tool_registry.register(echo_tool)

    gate = ConsequentialActionGate(confirm_fn=lambda _name: True)
    # Build with consent gate (needed for consequential tool dispatch safety).
    preg = ProviderRegistry()
    preg.register_mock("secretary", answer_provider, tier="powerful")
    preg.register_mock("powerful", answer_provider, tier="powerful")
    router = _StandardRouterProvider()
    preg.register_mock("router", router, tier="fast")
    preg.register_mock("local-judge", router, tier="local")
    services_with_gate = StepServices(
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
        consent_gate=gate,
    )
    backend = AsyncioBackend(services=services_with_gate)

    with caplog.at_level(logging.INFO, logger="stackowl.engine"):
        delivered, final_state = await _execute_turn(
            "what is the capital of France?",
            "sess-standard-fr6",
            "trace-standard-fr6-1",
            backend,
        )

    # ===================================================================
    # No-error guard — a floor-on-error reply must not silently pass.
    # ===================================================================
    assert not final_state.errors, f"unexpected errors: {final_state.errors}"

    # ===================================================================
    # FR6 OUTCOME 1 — the answer was delivered (sanity check).
    # ===================================================================
    assert delivered.strip(), (
        f"FR6 FAIL: delivered text is empty. Got: {delivered!r}"
    )

    # ===================================================================
    # FR6 OUTCOME 2 — budget log shows tools_used==True (standard path).
    # ===================================================================
    budget_records = [
        r for r in caplog.records
        if "[pipeline] execute: context budget" in r.getMessage()
    ]
    assert budget_records, (
        f"FR6 FAIL: '[pipeline] execute: context budget' log record not found. "
        f"Records: {[r.getMessage() for r in caplog.records]}"
    )
    budget_rec = budget_records[0]
    fields: dict[str, Any] = getattr(budget_rec, "_fields", {})

    assert fields.get("intent_class") != "conversational", (
        f"FR6 FAIL: intent_class in budget log is 'conversational' for a standard turn. "
        f"Got _fields: {fields!r}"
    )
    assert fields.get("tools_used") is True, (
        f"FR6 FAIL: tools_used in budget log is not True — standard turn did NOT enter "
        f"the tool loop. Got _fields: {fields!r}"
    )

    # ===================================================================
    # FR6 OUTCOME 3 — the tool loop WAS entered (the log proves it).
    # ===================================================================
    assert any("tool_loop entry" in r.getMessage() for r in caplog.records), (
        f"FR6 FAIL: 'tool_loop entry' log record NOT found — the standard turn did NOT "
        f"enter the tool loop. Records: {[r.getMessage() for r in caplog.records]}"
    )
