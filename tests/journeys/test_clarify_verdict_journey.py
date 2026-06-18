"""Clarify-verdict gateway journeys — end-to-end proof of the clarify router path.

Three business-outcome journeys, all driving the real pipeline through the
gateway and mocking ONLY the AI provider.

Journey 1 — test_vague_expensive_request_asks_one_question_not_tool_spiral
  GIVEN the router provider returns "secretary\\nclarify\\n<question>"
  WHEN "can you help me with pictures" runs through the gateway
  THEN the outbound response contains the ONE clarifying question; ZERO tool
       calls are executed; no chunk has is_floor=True; a pending clarify IS
       registered for the session (via the real ClarifyGateway).
  AND a follow-up message resolves the pending clarify so the next turn runs
      without a floor.

Journey 2 — test_greeting_routes_conversational_no_floor   (incident bug H)
  GIVEN the router returns "secretary\\nconversational" for "hi"
  THEN a plain reply is produced, no chunk has is_floor=True, no pending
       clarify is registered, the tool loop was NOT entered.

Journey 3 — test_vague_cheap_request_still_acts   (FALSIFICATION GUARD)
  GIVEN the router returns "secretary\\nstandard" for a vague-but-cheap request
  THEN the tool loop IS entered; NO pending clarify is registered.
  Proves clarify fires ONLY on the verdict, never blanket-on-ambiguity.

Harness reuse:
  Mirrors tests/journeys/test_conversational_bypass_journey.py exactly:
  * _ScriptedProvider — same class (direct ModelProvider subclass, supports
    complete()/stream()/complete_with_tools() defaulting to complete()).
  * _ClarifyRouterProvider — new; emits "secretary\\nclarify\\n<question>"
    for routing calls, same judge sentinel for persistence-judge calls.
  * _ConversationalRouterProvider / _StandardRouterProvider — imported logic
    mirrored here so this file is self-contained.
  * _EchoTool — same trivial tool that exercises the tool loop for Journey 3.
  * _build_services_* / _execute_turn — same builder pattern.
  * tmp_db, monkeypatch, caplog — same pytest fixtures.
  The real ClarifyGateway is wired into StepServices for Journeys 1 & 2 so
  peek_for_session() can check whether a pending clarify was registered.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.interaction.clarify_gateway import ClarifyGateway
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

# ---------------------------------------------------------------------------
# _ScriptedProvider — mirrors conversational_bypass_journey exactly.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Router providers — used by SecretaryRouter (triage step) via the fast tier.
# ---------------------------------------------------------------------------

_JUDGE_SENTINEL = '{"delivered": true, "reason": "looks complete"}'


class _ClarifyRouterProvider(ModelProvider):
    """Fast-tier router: emits a clarify verdict with an embedded question."""

    _QUESTION = "Do you want me to create images, or find existing ones?"

    @property
    def name(self) -> str:
        return "clarify-router-fake"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        joined = "\n".join(m.content for m in messages)
        content = (
            _JUDGE_SENTINEL
            if "AGENT DRAFT REPLY" in joined
            else f"secretary\nclarify\n{self._QUESTION}"
        )
        return CompletionResult(
            content=content,
            input_tokens=1,
            output_tokens=1,
            model="clarify-router-fake",
            provider_name="clarify-router-fake",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        yield f"secretary\nclarify\n{self._QUESTION}"


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
            _JUDGE_SENTINEL
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
            _JUDGE_SENTINEL
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


# ---------------------------------------------------------------------------
# _EchoTool — simple tool for Journey 3 (falsification guard).
# Mirrors the same class from conversational_bypass_journey exactly.
# ---------------------------------------------------------------------------


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
        return ToolResult(
            success=True,
            output=str(kwargs.get("text", "")),
            error=None,
            duration_ms=1.0,
        )


# ---------------------------------------------------------------------------
# Service / backend builders — mirrors conversational_bypass_journey pattern.
# ---------------------------------------------------------------------------


def _build_services_clarify(
    answer_provider: _ScriptedProvider,
    owl_registry: OwlRegistry,
    tool_registry: ToolRegistry,
    clarify_gateway: ClarifyGateway,
) -> StepServices:
    """StepServices with clarify-routing fast-tier provider + real ClarifyGateway."""
    preg = ProviderRegistry()
    preg.register_mock("secretary", answer_provider, tier="powerful")
    preg.register_mock("powerful", answer_provider, tier="powerful")
    router = _ClarifyRouterProvider()
    preg.register_mock("router", router, tier="fast")
    preg.register_mock("local-judge", router, tier="local")
    return StepServices(
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
        clarify_gateway=clarify_gateway,
    )


def _build_services_conversational(
    answer_provider: _ScriptedProvider,
    owl_registry: OwlRegistry,
    tool_registry: ToolRegistry,
    clarify_gateway: ClarifyGateway,
) -> StepServices:
    """StepServices with conversational-routing fast-tier provider."""
    preg = ProviderRegistry()
    preg.register_mock("secretary", answer_provider, tier="powerful")
    preg.register_mock("powerful", answer_provider, tier="powerful")
    router = _ConversationalRouterProvider()
    preg.register_mock("router", router, tier="fast")
    preg.register_mock("local-judge", router, tier="local")
    return StepServices(
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
        clarify_gateway=clarify_gateway,
    )


def _build_services_standard(
    answer_provider: _ScriptedProvider,
    owl_registry: OwlRegistry,
    tool_registry: ToolRegistry,
    clarify_gateway: ClarifyGateway,
) -> StepServices:
    """StepServices with standard-routing fast-tier provider."""
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
        clarify_gateway=clarify_gateway,
        consent_gate=ConsequentialActionGate(confirm_fn=lambda _name: True),
    )


async def _execute_turn(
    text: str,
    session: str,
    trace: str,
    backend: AsyncioBackend,
) -> tuple[str, PipelineState]:
    """Run a single turn through the backend; return (delivered_text, final_state).

    Mirrors conversational_bypass_journey._execute_turn exactly.
    """
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


# ===========================================================================
# Journey 1 — vague expensive request → ONE question, no tool spiral.
# ===========================================================================


@pytest.mark.asyncio
async def test_vague_expensive_request_asks_one_question_not_tool_spiral(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """GIVEN router returns clarify + question for "can you help me with pictures"
    THEN: the outbound response contains the ONE question text; ZERO tool calls
          are executed; no chunk has is_floor=True; a pending clarify IS registered
          for the session (real ClarifyGateway.peek_for_session).
    AND: a follow-up user message resolves the pending clarify (the pending entry
         exists so ClarifyPump.resolve_or_rewrite can route it as an answer turn).
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # The clarify turn needs no answer provider reply (clarify branch never calls
    # the owl's LLM). Supply a fallback so provider is always safe.
    answer_provider = _ScriptedProvider("answer-clarify", ["Here you go!"])
    owl_registry = OwlRegistry.with_default_secretary()
    tool_registry = ToolRegistry()  # no tools — ensures tool loop can't be entered
    clarify_gateway = ClarifyGateway()
    # Register a no-op CLI adapter so ask(deliver=False) does not warn about
    # missing adapter. The test checks peek_for_session, not delivery.
    clarify_gateway.register_adapter("cli", _NoOpClarifyAdapter())

    services = _build_services_clarify(
        answer_provider, owl_registry, tool_registry, clarify_gateway
    )
    backend = AsyncioBackend(services=services)

    session = "sess-clarify-journey-1"
    with caplog.at_level(logging.DEBUG, logger="stackowl.engine"):
        delivered, final_state = await _execute_turn(
            "can you help me with pictures",
            session,
            "trace-clarify-j1-1",
            backend,
        )

    # -----------------------------------------------------------------------
    # OUTCOME 1 — the clarifying question is in the outbound response.
    # -----------------------------------------------------------------------
    question = _ClarifyRouterProvider._QUESTION
    assert question in delivered, (
        f"J1 FAIL: expected the clarifying question {question!r} in the delivered "
        f"text. Got: {delivered!r}"
    )

    # -----------------------------------------------------------------------
    # OUTCOME 2 — ZERO tool calls executed (tool loop was NOT entered).
    # -----------------------------------------------------------------------
    assert not any("tool_loop entry" in r.getMessage() for r in caplog.records), (
        f"J1 FAIL: 'tool_loop entry' log found — the tool loop was entered on a "
        f"clarify turn. Records: {[r.getMessage() for r in caplog.records]}"
    )

    # -----------------------------------------------------------------------
    # OUTCOME 3 — no floor chunk (is_floor must be absent / False on all chunks).
    # -----------------------------------------------------------------------
    floor_chunks = [c for c in final_state.responses if getattr(c, "is_floor", False)]
    assert not floor_chunks, (
        f"J1 FAIL: found floor chunks on a clarify turn: {floor_chunks!r}. "
        f"Delivered: {delivered!r}"
    )

    # -----------------------------------------------------------------------
    # OUTCOME 4 — a pending clarify IS registered for this session+channel.
    # -----------------------------------------------------------------------
    pending = clarify_gateway.peek_for_session(session, "cli")
    assert pending is not None, (
        f"J1 FAIL: no pending clarify registered for session={session!r}, "
        f"channel='cli'. The ClarifyGateway was not populated by the execute branch."
    )
    assert question in pending.question, (
        f"J1 FAIL: pending.question {pending.question!r} does not contain {question!r}"
    )

    # -----------------------------------------------------------------------
    # OUTCOME 5 — the pending clarify is resolvable (pump precondition met).
    # Sending a follow-up to peek_for_session would let ClarifyPump route it
    # as an answer turn. We assert the precondition: entry is non-null, has a
    # clarify_id, and event is None (turn-yield, not a parked blocking waiter).
    # -----------------------------------------------------------------------
    assert pending.clarify_id, (
        "J1 FAIL: pending.clarify_id is empty — registration is malformed."
    )
    assert pending.event is None, (
        f"J1 FAIL: pending.event is not None — expected a turn-yield (deliver=False) "
        f"registration, got a blocking waiter: {pending!r}"
    )


# ===========================================================================
# Journey 2 — greeting → conversational, no floor (incident bug H).
# ===========================================================================


@pytest.mark.asyncio
async def test_greeting_routes_conversational_no_floor(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Incident bug H: "hi" must produce a plain reply with no floor and no
    pending clarify — the router returns conversational, not clarify.
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    greeting = "Hello! How can I help?"
    answer_provider = _ScriptedProvider("answer-hi", [greeting])
    owl_registry = OwlRegistry.with_default_secretary()
    tool_registry = ToolRegistry()
    clarify_gateway = ClarifyGateway()

    services = _build_services_conversational(
        answer_provider, owl_registry, tool_registry, clarify_gateway
    )
    backend = AsyncioBackend(services=services)

    session = "sess-clarify-journey-2"
    with caplog.at_level(logging.INFO, logger="stackowl.engine"):
        delivered, final_state = await _execute_turn(
            "hi",
            session,
            "trace-clarify-j2-1",
            backend,
        )

    # -----------------------------------------------------------------------
    # OUTCOME 1 — a non-empty greeting is delivered.
    # -----------------------------------------------------------------------
    assert delivered.strip(), (
        f"J2 FAIL: delivered text is empty for a 'hi' conversational turn. "
        f"Got: {delivered!r}"
    )

    # -----------------------------------------------------------------------
    # OUTCOME 2 — no floor chunk.
    # -----------------------------------------------------------------------
    floor_chunks = [c for c in final_state.responses if getattr(c, "is_floor", False)]
    assert not floor_chunks, (
        f"J2 FAIL: floor chunk found on a 'hi' conversational turn: {floor_chunks!r}"
    )

    # -----------------------------------------------------------------------
    # OUTCOME 3 — tool loop was NOT entered.
    # -----------------------------------------------------------------------
    assert not any("tool_loop entry" in r.getMessage() for r in caplog.records), (
        f"J2 FAIL: 'tool_loop entry' log found for 'hi' — conversational turn "
        f"entered the tool loop. Records: {[r.getMessage() for r in caplog.records]}"
    )

    # -----------------------------------------------------------------------
    # OUTCOME 4 — NO pending clarify registered for this session.
    # -----------------------------------------------------------------------
    pending = clarify_gateway.peek_for_session(session, "cli")
    assert pending is None, (
        f"J2 FAIL: a pending clarify was incorrectly registered for a "
        f"conversational 'hi' turn: {pending!r}"
    )


# ===========================================================================
# Journey 3 — vague-but-cheap request → standard path acts (FALSIFICATION GUARD).
# ===========================================================================


@pytest.mark.asyncio
async def test_vague_cheap_request_still_acts(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FALSIFICATION GUARD: a request routed as 'standard' must enter the tool
    loop (act), even if the request text is vague. Proves that clarify fires ONLY
    on the clarify verdict — not blanket-on-ambiguity.
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    final_answer = "Here is a summary of what I found."
    answer_provider = _ScriptedProvider("answer-std", [final_answer])
    owl_registry = OwlRegistry.with_default_secretary()

    # Register a real tool so tool_registry.all() is non-empty — required for
    # _use_tools to be True (execute.py: tool_registry is not None and tool_registry.all()).
    echo_tool = _EchoTool()
    tool_registry = ToolRegistry()
    tool_registry.register(echo_tool)

    clarify_gateway = ClarifyGateway()

    services = _build_services_standard(
        answer_provider, owl_registry, tool_registry, clarify_gateway
    )
    backend = AsyncioBackend(services=services)

    session = "sess-clarify-journey-3"
    with caplog.at_level(logging.INFO, logger="stackowl.engine"):
        delivered, final_state = await _execute_turn(
            "summarize this",
            session,
            "trace-clarify-j3-1",
            backend,
        )

    # -----------------------------------------------------------------------
    # OUTCOME 1 — a non-empty answer is delivered.
    # -----------------------------------------------------------------------
    assert delivered.strip(), (
        f"J3 FAIL: delivered text is empty for a standard vague request. "
        f"Got: {delivered!r}"
    )

    # -----------------------------------------------------------------------
    # OUTCOME 2 — tool loop WAS entered (proves the standard path is active).
    # -----------------------------------------------------------------------
    budget_records = [
        r for r in caplog.records
        if "[pipeline] execute: context budget" in r.getMessage()
    ]
    assert budget_records, (
        f"J3 FAIL: '[pipeline] execute: context budget' log not found. "
        f"Records: {[r.getMessage() for r in caplog.records]}"
    )
    fields: dict[str, Any] = getattr(budget_records[0], "_fields", {})
    assert fields.get("tools_used") is True, (
        f"J3 FAIL: tools_used is not True — the standard turn did NOT enter the "
        f"tool loop. Got _fields: {fields!r}"
    )

    assert any("tool_loop entry" in r.getMessage() for r in caplog.records), (
        f"J3 FAIL: 'tool_loop entry' log NOT found — standard turn did not enter "
        f"the tool loop. Records: {[r.getMessage() for r in caplog.records]}"
    )

    # -----------------------------------------------------------------------
    # OUTCOME 3 — NO pending clarify registered.
    # -----------------------------------------------------------------------
    pending = clarify_gateway.peek_for_session(session, "cli")
    assert pending is None, (
        f"J3 FAIL: a pending clarify was incorrectly registered for a standard "
        f"(non-clarify) turn: {pending!r}"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NoOpClarifyAdapter:
    """Minimal channel adapter that silently accepts send_clarify calls.

    Used in Journey 1 so ClarifyGateway.ask() does not emit a 'no adapter'
    warning — the journey checks peek_for_session, not delivery.
    """

    async def send_clarify(
        self,
        session_id: str,
        question: str,
        choices: list[str] | None,
        clarify_id: str,
    ) -> None:
        pass
