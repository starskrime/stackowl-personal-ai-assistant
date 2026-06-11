"""Self-Healing Turn Supervisor — capability-substitution merge gates (W3.T15).

The END-TO-END proof of W3: when a tool in a capability class FAILS mid-turn, the
dispatch actuator deterministically routes around the broken primary to an
in-bounds, NON-consequential sibling that produces the same KIND of result, runs
it through the SAME guarded path, and feeds the sibling's SUCCESS back as the
observation — so the agent DELIVERS using the working sibling instead of
surrendering to the failure. Bounded to one substitution per capability-tag per
turn; a consequential sibling is NEVER auto-run (consent-safe by construction).

Two gates, driven through the REAL gateway (Dr. Quinn's "use the working 30%"):

  Test 1 — ROUTE-AROUND (the headline W3 proof). A consequential primary
  (``browser_browse``) FAILS; its read sibling (``web_search``) succeeds with a
  distinctive payload. ASSERT the OUTCOME: the user's FINAL answer contains the
  sibling-derived data ("sunny 24C"), the sibling actually RAN, and the primary's
  failure did NOT end the turn.

  Test 2 — CONSENT-SAFETY (the security proof). A read primary (``web_search``)
  FAILS; its ONLY same-tag sibling is CONSEQUENTIAL (``browser_browse``). ASSERT:
  the consequential sibling was NEVER auto-executed (no consent bypass); the turn
  falls through to the honest TOOL_FAILED observation.

REAL (everything except the AI provider): the whole AsyncioBackend pipeline
(scanner → triage → execute → deliver), the REAL ``ToolRegistry`` + ``_dispatch``
+ ``_try_substitute`` → ``find_substitute`` actuator, the REAL bounds + consent +
ledger-guard seam, and the REAL ``OpenAIProvider.complete_with_tools`` ReAct loop.
FAKED: ONLY the AI provider (a scripted fake OpenAI SDK client driving the real
``OpenAIProvider``) and the triage-router/judge provider on the fast+local tiers.

The custom tools use the REAL adapter names (``browser_browse`` / ``web_search``)
so the declarative ``capability_substitution`` adapters apply; the substitution
logic is otherwise fully real. Mirrors the gateway construction in
``tests/journeys/test_self_heal_lying_judge.py`` (scripted provider + real
pipeline) — flipped from the structural-veto slice to the substitution slice.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

# --------------------------------------------------------------------------- #
# Custom capability-class tools. They use the REAL adapter names
# (browser_browse / web_search) so the declarative web_knowledge adapters in
# stackowl.pipeline.capability_substitution build the sibling's args. severity,
# capability_tag, success/output are parameterized per test.
# --------------------------------------------------------------------------- #


class _CapabilityTool(Tool):
    """A web_knowledge-class tool with a controllable severity + success."""

    def __init__(
        self,
        name: str,
        *,
        severity: str,
        capability_tag: str | None,
        output: str,
        succeed: bool,
        params: dict[str, object],
    ) -> None:
        self._name = name
        self._severity = severity
        self._tag = capability_tag
        self._output = output
        self._succeed = succeed
        self._params = params
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"web_knowledge capability: {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return self._params

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name,
            description=self.description,
            parameters=self._params,
            action_severity=self._severity,  # type: ignore[arg-type]
            capability_tag=self._tag,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        if self._succeed:
            return ToolResult(success=True, output=self._output, error=None, duration_ms=1.0)
        return ToolResult(
            success=False, output="", error="capability unavailable", duration_ms=1.0
        )


_WEB_TAG = "web_knowledge"


# --------------------------------------------------------------------------- #
# The triage-router / persistence-judge provider (fast + local tiers). Returns
# the owl name for triage routing, and {delivered:true} on a judge prompt so the
# persistence checker is fail-open (contributes nothing) — this test is about the
# substitution actuator, not the give-up veto. Mirrors test_self_heal_lying_judge.
# --------------------------------------------------------------------------- #


class _RouterJudgeProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "router-judge-fake"

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
            else "secretary"
        )
        return CompletionResult(
            content=content,
            input_tokens=1,
            output_tokens=1,
            model="router-judge-fake",
            provider_name="router-judge-fake",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        yield "secretary"


# --------------------------------------------------------------------------- #
# Fake OpenAI SDK client (shape from test_self_heal_lying_judge) driving a REAL
# OpenAIProvider through its ReAct text loop.
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
        self.calls: list[list[dict[str, Any]]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append([dict(m) for m in kwargs["messages"]])
        idx = min(self._i, len(self._responses) - 1)
        resp = self._responses[idx]
        self._i += 1
        return resp


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.chat = _FakeChat(_FakeCompletions(responses))


def _make_main_provider(client: _FakeClient) -> OpenAIProvider:
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


def _build_services(
    provider: OpenAIProvider,
    owl_registry: OwlRegistry,
    tool_registry: ToolRegistry,
    *,
    consent_gate: ConsequentialActionGate | None = None,
) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    router = _RouterJudgeProvider()
    preg.register_mock("router", router, tier="fast")
    preg.register_mock("local-judge", router, tier="local")
    return StepServices(
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
        consent_gate=consent_gate,
    )


def _run_state(backend_input: str, session: str, trace: str) -> tuple[GatewayScanner, IngressMessage]:
    scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())
    msg = IngressMessage(
        text=backend_input,
        session_id=session,
        channel="cli",
        trace_id=trace,
    )
    return scanner, msg


# =========================================================================== #
# Test 1 — ROUTE-AROUND end-to-end (the headline W3 proof).
# =========================================================================== #


@pytest.mark.asyncio
async def test_substitution_route_around_delivers_sibling_data_end_to_end(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A consequential primary FAILS mid-turn; the dispatch actuator routes around
    it to a READ sibling that succeeds, and the user's FINAL answer carries the
    sibling-derived data. Drives the REAL pipeline + dispatch actuator; mocks ONLY
    the AI provider. Proves the agent ROUTED AROUND the broken primary and
    DELIVERED using the working sibling."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    sibling_payload = "WEATHER: sunny 24C"

    # browser_browse (consequential) = the broken primary. web_search (read) = the
    # working sibling. The consequential primary is user-approved (auto-approving
    # consent gate) so it RUNS and FAILS, arming the substitution — modeling a
    # consequential action that failed mid-loop, then was routed around.
    primary = _CapabilityTool(
        "browser_browse",
        severity="consequential",
        capability_tag=_WEB_TAG,
        output="BROWSE_OK",
        succeed=False,
        params={"type": "object", "properties": {"task": {"type": "string"}}},
    )
    sibling = _CapabilityTool(
        "web_search",
        severity="read",
        capability_tag=_WEB_TAG,
        output=sibling_payload,
        succeed=True,
        params={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    tool_registry = ToolRegistry()
    tool_registry.register(primary)
    tool_registry.register(sibling)

    # Iter 0: call the failing primary with a query/task. _dispatch runs it → it
    #         FAILS → _try_substitute picks web_search (read, same tag) → runs it →
    #         returns "WEATHER: sunny 24C" as the OBSERVATION.
    # Iter 1: the model produces a FINAL answer USING the sibling's data.
    call_primary = _FakeMessage(
        content=(
            "ACTION: browser_browse\n```json\n"
            '{"task": "weather today in Paris"}\n```'
        ),
        tool_calls=None,
    )
    final_answer = _FakeMessage(
        content="Based on what I found, the weather today is sunny 24C.",
        tool_calls=None,
    )
    client = _FakeClient([_FakeResponse(call_primary), _FakeResponse(final_answer)])
    provider = _make_main_provider(client)

    owl_registry = OwlRegistry.with_default_secretary()
    # Auto-approving consent gate: the consequential primary is approved, so it
    # runs and fails (arming substitution). The substitution's OWN consent-safety
    # is enforced inside find_substitute (severity filter) — proven by Test 2.
    gate = ConsequentialActionGate(confirm_fn=lambda _name: True)
    services = _build_services(
        provider, owl_registry, tool_registry, consent_gate=gate
    )
    backend = AsyncioBackend(services=services)

    scanner, msg = _run_state(
        "what's the weather today?", "sess-sub-route", "trace-sub-route-1"
    )
    decision = scanner.scan(msg)
    input_text = (
        decision.stripped_text if decision.stripped_text is not None else msg.text
    )
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

    # ===================================================================
    # OUTCOME 1 — the user's FINAL answer carries the SIBLING-derived data.
    # The agent routed around the broken primary and DELIVERED using the
    # working sibling's result.
    # ===================================================================
    assert "sunny 24C" in delivered, (
        "MERGE-GATE FAIL: the delivered answer does not contain the sibling-derived "
        f"data ('sunny 24C') — the route-around did not feed the working sibling's "
        f"result back to the model. Delivered: {delivered!r}"
    )

    # ===================================================================
    # OUTCOME 2 — the READ sibling actually RAN, with adapter-built args
    # (the {task: ...} from the failed primary was normalized to {query: ...}).
    # ===================================================================
    assert sibling.calls == [{"query": "weather today in Paris"}], (
        "MERGE-GATE FAIL: the read sibling did not run with the adapter-built args — "
        f"the dispatch actuator did not execute the substitute. Calls: {sibling.calls}"
    )

    # ===================================================================
    # OUTCOME 3 — the broken primary's FAILURE did NOT end the turn: the loop
    # made a 2nd provider call (the post-substitution final-answer round). The
    # primary genuinely ran and failed (1 call) — that is what armed the actuator.
    # ===================================================================
    assert primary.calls, (
        "the primary never ran — the failure that arms the actuator never occurred"
    )
    assert len(client.chat.completions.calls) == 2, (
        "MERGE-GATE FAIL: the loop did not continue past the tool failure — the "
        "substitution's observation did not let the model produce a final answer. "
        f"Provider calls: {len(client.chat.completions.calls)}"
    )


# =========================================================================== #
# Test 2 — CONSENT-SAFETY end-to-end (the security proof).
# =========================================================================== #


@pytest.mark.asyncio
async def test_substitution_never_auto_runs_consequential_sibling_end_to_end(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read primary FAILS; its ONLY same-tag sibling is CONSEQUENTIAL. The
    actuator NEVER auto-runs the consequential sibling (no consent bypass); the
    turn falls through to the honest TOOL_FAILED observation. Drives the REAL
    pipeline + dispatch actuator; mocks ONLY the AI provider."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # web_search (read) = the failing primary (runs without consent). browser_browse
    # (consequential) = the ONLY same-tag sibling — must NEVER be auto-run.
    primary = _CapabilityTool(
        "web_search",
        severity="read",
        capability_tag=_WEB_TAG,
        output="SEARCH_OK",
        succeed=False,
        params={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    consequential_sibling = _CapabilityTool(
        "browser_browse",
        severity="consequential",
        capability_tag=_WEB_TAG,
        output="SHOULD_NEVER_RUN",
        succeed=True,
        params={"type": "object", "properties": {"task": {"type": "string"}}},
    )
    tool_registry = ToolRegistry()
    tool_registry.register(primary)
    tool_registry.register(consequential_sibling)

    # Iter 0: call the failing read primary. _dispatch runs it → it FAILS →
    #         _try_substitute finds ONLY a consequential sibling → find_substitute
    #         excludes it → returns None → falls through to the TOOL_FAILED marker.
    # Iter 1: the model produces a final answer acknowledging the failure (it could
    #         still try the consequential tool WITH the normal consent gate, but the
    #         ACTUATOR did not auto-run it).
    call_primary = _FakeMessage(
        content='ACTION: web_search\n```json\n{"query": "weather today"}\n```',
        tool_calls=None,
    )
    final_answer = _FakeMessage(
        content="I couldn't retrieve that information right now.",
        tool_calls=None,
    )
    client = _FakeClient([_FakeResponse(call_primary), _FakeResponse(final_answer)])
    provider = _make_main_provider(client)

    owl_registry = OwlRegistry.with_default_secretary()
    # An auto-approving gate is present to PROVE the actuator's consent-safety is
    # structural (severity filter in find_substitute), NOT merely an artifact of a
    # missing/denying gate: even with a gate that WOULD approve, the actuator never
    # routes to the consequential sibling.
    gate = ConsequentialActionGate(confirm_fn=lambda _name: True)
    services = _build_services(
        provider, owl_registry, tool_registry, consent_gate=gate
    )
    backend = AsyncioBackend(services=services)

    scanner, msg = _run_state(
        "what's the weather today?", "sess-sub-consent", "trace-sub-consent-1"
    )
    decision = scanner.scan(msg)
    input_text = (
        decision.stripped_text if decision.stripped_text is not None else msg.text
    )
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

    # ===================================================================
    # OUTCOME (the security proof) — the CONSEQUENTIAL sibling was NEVER
    # auto-executed. No consent bypass: the actuator refused to route to it.
    # ===================================================================
    assert consequential_sibling.calls == [], (
        "MERGE-GATE FAIL (SECURITY): the consequential sibling was AUTO-RUN by the "
        "substitution actuator — a consent bypass. It must NEVER be auto-executed. "
        f"Calls: {consequential_sibling.calls}"
    )

    # Wiring sanity: the read primary genuinely RAN and FAILED (that is what gave
    # the actuator the chance to — and it correctly DID NOT — substitute).
    assert primary.calls, (
        "the read primary never ran — the failure that exercises the actuator never "
        "occurred, so the consent-safety assertion would be vacuous"
    )

    # The turn fell through to the honest TOOL_FAILED observation: the model got a
    # 2nd round (the failure was surfaced, not silently substituted away), and the
    # sibling's secret output never leaked into the conversation.
    assert "SHOULD_NEVER_RUN" not in delivered, (
        "MERGE-GATE FAIL (SECURITY): the consequential sibling's output leaked into "
        "the delivered answer — it must never have run."
    )
    assert len(client.chat.completions.calls) == 2, (
        "MERGE-GATE FAIL: the loop did not make the expected 2 provider calls — the "
        "TOOL_FAILED fall-through did not let the model produce its final answer. "
        f"Provider calls: {len(client.chat.completions.calls)}"
    )
