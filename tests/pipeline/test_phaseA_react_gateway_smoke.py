"""Gateway-level integration smoke: weak-model ReAct tool dispatch end-to-end (Phase A5).

This proves the WHOLE "a model that emits NO native tool_calls still uses a tool"
chain is wired on the REAL production path — not just the parser in isolation:

    GatewayScanner.scan
      -> secretary route
      -> PipelineState (built the way startup/orchestrator.py builds it)
      -> AsyncioBackend.run
      -> pipeline/steps/execute._run_with_tools (tool-loop branch, forced by a
         REAL non-empty ToolRegistry)
      -> OpenAIProvider.complete_with_tools  (the REAL provider instance)
      -> the Phase A1 ReAct text fallback (no native tool_calls -> parse ACTION:)
      -> execute._dispatch  (the dispatch chokepoint)
      -> a REAL registered tool  (records that it ran + returns a marker)
      -> OBSERVATION fed back to the model
      -> final answer delivered to the user.

It ALSO asserts the assembled system prompt carries the Phase A2-A4 agentic base
(the live date's current year + the ``ACTION:`` tool-use mandate), proving the
base prompt is wired through ``assemble`` and reaches the provider.

Design (mirrors ``tests/providers/test_react_protocol.py`` for the fake SDK
client, and ``tests/pipeline/test_plan_a_gateway_integration.py`` for the
gateway-driving harness):

  * The provider is a REAL ``OpenAIProvider`` whose ``_client`` is replaced by a
    fake whose ``chat.completions.create`` returns canned responses with
    ``tool_calls = None`` and ACTION text — so the test exercises the genuine
    ReAct branch in ``openai_provider.py``. Removing that branch (returning the
    raw content instead of parsing ACTION:) makes this test FAIL: the tool is
    never dispatched and no OBSERVATION round-trips.
  * The real ``OpenAIProvider`` is registered through the real ``ProviderRegistry``
    under both the per-owl key (``secretary``) AND the ``powerful`` tier, so
    whichever lookup ``execute.run`` takes resolves to it (mirrors the reference
    harness's dual registration).
  * A REAL, non-empty ``ToolRegistry`` (containing our target tool) forces the
    tool-loop branch instead of the streaming branch.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.infra.clock import now_local
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# Target tool — a tiny, deterministic, read-severity built-in for the test.
#
# We register a FAKE read tool (rather than target a real built-in) for three
# reasons: (1) determinism — no external deps / network / filesystem to make the
# OBSERVATION non-deterministic; (2) an unambiguous "it ran" signal (the tool
# records its invocation); (3) a unique marker string we can assert flows all the
# way to the delivered answer. It is ``action_severity="read"`` (the default), so
# with no consent gate wired the dispatch chokepoint permits it (only
# *consequential* tools fail closed without a gate).
# --------------------------------------------------------------------------- #

_TOOL_NAME = "lookup_latest"
_TOOL_MARKER = "LATEST-HEADLINE-2026-XYZZY"


class _LookupLatestTool(Tool):
    """Deterministic read tool: returns a fixed marker and records that it ran."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return _TOOL_NAME

    @property
    def description(self) -> str:
        return "Look up the latest information."

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        return ToolResult(
            success=True,
            output=f"{_TOOL_MARKER} (query={kwargs.get('query')!r})",
            error=None,
            duration_ms=0.0,
        )


# --------------------------------------------------------------------------- #
# Fake OpenAI SDK client — records the `messages` it is called with each turn so
# we can assert the OBSERVATION round-tripped into the second invocation.
# (Same shape as tests/providers/test_react_protocol.py.)
# --------------------------------------------------------------------------- #


class _FakeMessage:
    def __init__(self, content: str | None, tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]
        self.model = "gemma4:e4b"


class _FakeCompletions:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self._i = 0
        # Per-invocation snapshot of the `messages` list (shallow-copied so later
        # in-place mutation by the provider loop cannot corrupt what we recorded).
        self.calls: list[list[dict[str, Any]]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append([dict(m) for m in kwargs["messages"]])
        resp = self._responses[self._i]
        self._i += 1
        return resp


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.chat = _FakeChat(_FakeCompletions(responses))


# --------------------------------------------------------------------------- #
# Routing provider — the ``triage`` step runs the SecretaryRouter (an LLM intent
# classifier) which calls ``complete()`` on the FAST-tier provider. We give it a
# dedicated tiny provider that deterministically routes to ``secretary`` so the
# router's ``complete()`` call does NOT consume the ReAct fake client's sequenced
# responses (those are reserved for the execute step's ``complete_with_tools``).
# --------------------------------------------------------------------------- #


class _RoutingProvider(ModelProvider):
    """Fast-tier provider whose ``complete`` deterministically routes to secretary."""

    @property
    def name(self) -> str:
        return "routing-fake"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        return CompletionResult(
            content="secretary",
            input_tokens=1,
            output_tokens=1,
            model="routing-fake",
            provider_name="routing-fake",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        yield "secretary"


# --------------------------------------------------------------------------- #
# Judge provider — the execute step's persistence give-up judge resolves the
# config-driven ``judge_tier`` (default "standard") and, on its failure, the
# "local" tier, calling ``complete()`` to rule deliver-vs-give-up. Without a
# dedicated provider at those tiers the judge cascades onto the ReAct fake's
# ``powerful`` provider and CONSUMES one of its sequenced responses (the
# ACTION/final pair), exhausting the list. Give the judge its own deterministic
# provider — exactly the _RoutingProvider rationale — that rules DELIVERED, which
# is the truth here (the draft incorporates the tool marker). It returns the
# verdict JSON ``judge_delivery`` parses; it never touches the ReAct responses.
# --------------------------------------------------------------------------- #


class _JudgeProvider(ModelProvider):
    """Standard/local-tier provider whose ``complete`` rules the draft DELIVERED."""

    @property
    def name(self) -> str:
        return "judge-fake"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        return CompletionResult(
            content='{"delivered": true, "reason": "draft carries the tool result"}',
            input_tokens=1,
            output_tokens=1,
            model="judge-fake",
            provider_name="judge-fake",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        yield ""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _build_services(
    bridge: SqliteMemoryBridge,
    provider: OpenAIProvider,
    owl_registry: OwlRegistry,
    tool_registry: ToolRegistry,
) -> StepServices:
    preg = ProviderRegistry()
    # Resolve the SAME real provider under the per-owl key ("secretary") AND the
    # "powerful" tier, so whichever lookup execute.run takes lands on it (mirrors
    # the reference gateway-integration harness).
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    # The triage step's SecretaryRouter resolves the FAST tier via get_with_cascade
    # and calls complete() on it. Give it a dedicated routing provider so it does
    # NOT consume the ReAct fake client's sequenced (ACTION/final) responses.
    preg.register_mock("router", _RoutingProvider(), tier="fast")
    # The persistence give-up judge resolves judge_tier ("standard") then "local";
    # give it a dedicated provider at both so it rules DELIVERED without cascading
    # onto — and consuming a response from — the ReAct fake's "powerful" provider.
    preg.register_mock("judge-standard", _JudgeProvider(), tier="standard")
    preg.register_mock("judge-local", _JudgeProvider(), tier="local")
    return StepServices(
        memory_bridge=bridge,
        provider_registry=preg,
        owl_registry=owl_registry,
        # REAL, non-empty tool_registry => execute.run takes the TOOL-LOOP branch.
        tool_registry=tool_registry,
        # consent_gate left None: the target tool is read-severity, so dispatch
        # permits it (only consequential tools fail-closed without a gate).
    )


def _state_from_decision(
    decision: Any, *, trace_id: str, session_id: str, channel: str, raw_text: str
) -> PipelineState:
    """Build PipelineState exactly as startup/orchestrator.py does for an owl route."""
    input_text = decision.stripped_text if decision.stripped_text is not None else raw_text
    return PipelineState(
        trace_id=trace_id,
        session_id=session_id,
        input_text=input_text,
        channel=channel,
        owl_name=decision.target,
        pipeline_step="start",
        interactive=True,
    )


def _make_real_provider(client: _FakeClient) -> OpenAIProvider:
    """A REAL OpenAIProvider with its SDK client swapped for the fake recorder."""
    config = ProviderConfig(
        name="ollama",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="gemma4:e4b",
        tier="powerful",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = client  # type: ignore[assignment]
    return provider


# --------------------------------------------------------------------------- #
# Test
# --------------------------------------------------------------------------- #


async def test_weak_model_react_tool_dispatch_through_gateway(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Neutralize the test-mode guard exactly as the existing provider/gateway tests
    # do (complete_with_tools and the real tool both call assert_not_test_mode).
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # --- Fake SDK responses: a NO-native-tool_calls weak model -----------------
    # Call 1: tool_calls=None, content carries an ACTION block targeting our tool.
    react_msg = _FakeMessage(
        content=(
            "I'll look that up.\n"
            f"ACTION: {_TOOL_NAME}\n"
            "```json\n"
            '{"query": "latest news"}\n'
            "```"
        ),
        tool_calls=None,
    )
    # Call 2: tool_calls=None, a plain final answer incorporating the observation.
    final_msg = _FakeMessage(
        content=f"Based on the result: {_TOOL_MARKER} is the latest.",
        tool_calls=None,
    )
    client = _FakeClient([_FakeResponse(react_msg), _FakeResponse(final_msg)])
    provider = _make_real_provider(client)

    # --- Real wiring -----------------------------------------------------------
    bridge = SqliteMemoryBridge(db=tmp_db)
    owl_registry = OwlRegistry.with_default_secretary()
    tool = _LookupLatestTool()
    tool_registry = ToolRegistry()
    tool_registry.register(tool)
    assert tool_registry.all(), "tool_registry must be non-empty to force tool-loop branch"

    services = _build_services(bridge, provider, owl_registry, tool_registry)
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=owl_registry)

    session_id = "sess-react-gw"

    # --- Drive the request THROUGH the gateway scanner -------------------------
    msg = IngressMessage(
        text="what's the latest?",
        session_id=session_id,
        channel="cli",
        trace_id="trace-react-gw-1",
    )
    decision = scanner.scan(msg)
    assert decision.route == "owl", f"expected owl route, got {decision.route!r}"
    assert decision.target == "secretary", f"expected secretary, got {decision.target!r}"

    state = _state_from_decision(
        decision,
        trace_id=msg.trace_id,
        session_id=session_id,
        channel=msg.channel,
        raw_text=msg.text,
    )
    final_state = await backend.run(state)

    # === Assertion 1: the target tool WAS dispatched =========================
    # The fake client's call-1 ACTION -> execute._dispatch -> the real tool ran.
    assert tool.calls, (
        "Assertion 1 FAIL: the target tool was never dispatched. The ReAct branch "
        "in openai_provider.complete_with_tools did not parse the ACTION block and "
        "call the dispatcher."
    )
    assert tool.calls[0].get("query") == "latest news", (
        f"Assertion 1 FAIL: tool dispatched with wrong args: {tool.calls[0]!r}"
    )

    # === Assertion 2: the OBSERVATION round-tripped ===========================
    # The provider should have made exactly two SDK calls; the 2nd invocation's
    # messages must include an OBSERVATION turn carrying the tool's result marker.
    assert len(client.chat.completions.calls) == 2, (
        "Assertion 2 FAIL: expected exactly 2 provider invocations "
        f"(ACTION turn + final answer turn), got {len(client.chat.completions.calls)}."
    )
    second_call_messages = client.chat.completions.calls[1]
    observation_turns = [
        m
        for m in second_call_messages
        if m.get("role") == "user" and "OBSERVATION:" in str(m.get("content", ""))
    ]
    assert observation_turns, (
        "Assertion 2 FAIL: no OBSERVATION turn was fed back on the 2nd provider call. "
        f"Messages: {second_call_messages!r}"
    )
    assert any(_TOOL_MARKER in str(m.get("content", "")) for m in observation_turns), (
        "Assertion 2 FAIL: the OBSERVATION turn did not carry the tool's result "
        f"marker {_TOOL_MARKER!r}. Observation turns: {observation_turns!r}"
    )

    # === Assertion 3: date + ACTION mandate reached the provider's system msg ==
    # Proves the Phase A2-A4 agentic base prompt was assembled and wired through.
    first_call_messages = client.chat.completions.calls[0]
    system_msgs = [m for m in first_call_messages if m.get("role") == "system"]
    assert system_msgs, "Assertion 3 FAIL: no system message reached the provider."
    system_text = str(system_msgs[0]["content"])
    current_year = str(now_local().year)
    assert current_year in system_text, (
        f"Assertion 3 FAIL: current year {current_year!r} (the live date) was not in "
        "the system prompt — the agentic base prompt's date line did not wire through."
    )
    assert "ACTION:" in system_text, (
        "Assertion 3 FAIL: the 'ACTION:' tool-use mandate was not in the system "
        "prompt — the Phase A2-A4 base prompt did not wire through assemble."
    )

    # === Assertion 4: the user got the call-2 final answer (non-empty) =========
    delivered = "".join(chunk.content for chunk in final_state.responses)
    assert delivered.strip(), (
        "Assertion 4 FAIL: the delivered response is empty — the user got silence "
        "instead of the model's final answer."
    )
    assert _TOOL_MARKER in delivered, (
        "Assertion 4 FAIL: the delivered final answer did not incorporate the tool "
        f"result marker {_TOOL_MARKER!r}. Delivered: {delivered!r}"
    )
    assert "Based on the result:" in delivered, (
        "Assertion 4 FAIL: the delivered answer is not the call-2 final answer. "
        f"Delivered: {delivered!r}"
    )
