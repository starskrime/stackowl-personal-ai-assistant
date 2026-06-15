"""F088 (P0) merge-gate — the dressed-up draft is NEVER persisted/promoted.

THE cluster-C4 P0 regression. A turn that FAILS a consequential action and gets
FLOORED must NOT persist the dressed-up "I did it" draft as a durable/promotable
fact — else the dream worker later promotes a lie into committed_facts.

Driven end-to-end through the REAL backend (parametrized over AsyncioBackend AND
LangGraphBackend so BOTH post-floor seams are covered — LM-1). Mocks ONLY the AI
provider. Asserts the user OUTCOME (delivered text) AND the persisted record
(staged facts), for each turn shape:

  * consequential give-up FLOORED  → delivered = honest floor; persisted record
    contains NO dressed-up draft (user utterance only). [asyncio — see note below]
  * clean success                  → delivered + persisted with trust="self". [both]

The tool-merge → trust="untrusted" path (SP-2) is unreachable end-to-end (execute
always emits a non-empty chunk, so consolidate's merge branch never fires live);
it is covered at the unit level — see tests/pipeline/test_consolidate_merged_external.py
and tests/pipeline/test_consolidate_trust.py.

Mirrors the harness from tests/journeys/test_no_dressed_up_giveup_journey.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.backends.langgraph_backend import LangGraphBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

# --------------------------------------------------------------------------- #
# Tools.
# --------------------------------------------------------------------------- #


class _ConsequentialTool(Tool):
    def __init__(self, name: str, *, succeed: bool) -> None:
        self._name = name
        self._succeed = succeed
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"consequential tool {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"target": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self.description, parameters=self.parameters,
            action_severity="consequential", capability_tag=None,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        if self._succeed:
            return ToolResult(success=True, output="done_ok", error=None, duration_ms=1.0)
        return ToolResult(success=False, output="", error="action failed: blocked", duration_ms=1.0)


# --------------------------------------------------------------------------- #
# Provider fakes (same shape as the no-dressed-up-giveup journey).
# --------------------------------------------------------------------------- #


class _RouterJudgeProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "router-judge-fake"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        joined = "\n".join(m.content for m in messages)
        content = (
            '{"delivered": true, "reason": "ok"}'
            if "AGENT DRAFT REPLY" in joined
            else "secretary"
        )
        return CompletionResult(
            content=content, input_tokens=1, output_tokens=1,
            model="router-judge-fake", provider_name="router-judge-fake", duration_ms=0.0,
        )

    async def stream(self, messages: list[Message], model: str, **kwargs: object):  # type: ignore[override]
        yield "secretary"


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

    async def create(self, **kwargs: Any) -> _FakeResponse:
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
        name="ollama", protocol="openai", base_url="http://localhost:11434/v1",
        default_model="gemma4:e4b", tier="powerful",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = client  # type: ignore[assignment]
    return provider


def _build_services(
    provider: OpenAIProvider, owl_registry: OwlRegistry, tool_registry: ToolRegistry,
    bridge: SqliteMemoryBridge, *, consent_gate: ConsequentialActionGate | None = None,
) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    router = _RouterJudgeProvider()
    preg.register_mock("router", router, tier="fast")
    preg.register_mock("local-judge", router, tier="local")
    preg.register_mock("fast", router, tier="fast")
    return StepServices(
        provider_registry=preg, owl_registry=owl_registry, tool_registry=tool_registry,
        memory_bridge=bridge, consent_gate=consent_gate,
    )


def _make_backend(kind: str, services: StepServices) -> AsyncioBackend | LangGraphBackend:
    if kind == "asyncio":
        return AsyncioBackend(services=services)
    return LangGraphBackend(services=services, use_memory_checkpoint=True)


async def _execute_turn(
    text: str, session: str, trace: str, backend: AsyncioBackend | LangGraphBackend,
) -> str:
    scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())
    msg = IngressMessage(text=text, session_id=session, channel="cli", trace_id=trace)
    decision = scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
        interactive=True,
    )
    final_state = await backend.run(state)
    return "".join(c.content for c in final_state.responses)


_BACKENDS = ["asyncio", "langgraph"]


# =========================================================================== #
# THE P0 — a floored consequential give-up must NOT persist the dressed-up draft.
# =========================================================================== #


# NOTE: this case runs on the AsyncioBackend (ARCH-114 production path). The
# consequential-giveup FLOOR depends on the turn-scoped tool_outcome_ledger
# ContextVar, which the LangGraph backend does not propagate between graph nodes
# (each node runs in a copied context, so the execute-node's recorded outcomes are
# invisible to the deliver-node's floor read). That is a PRE-EXISTING LangGraph
# context-propagation gap (the giveup floor shipped + was only ever tested on
# asyncio), not a C4 regression — see the C4 report. persist_turn's wiring on BOTH
# backends is proven by test_clean_success_persists_self_trust below.
@pytest.mark.asyncio
async def test_floored_turn_does_not_persist_dressed_up_draft(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_kind = "asyncio"
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    bridge = SqliteMemoryBridge(db=tmp_db)
    build = _ConsequentialTool("build_agentic_bridge", succeed=False)
    tool_registry = ToolRegistry()
    tool_registry.register(build)

    call_tool = _FakeMessage(
        content="ACTION: build_agentic_bridge\n```json\n{\"target\": \"x\"}\n```", tool_calls=None,
    )
    dressed_up = _FakeMessage(
        content="I have built the full agentic bridge for you. Here are the steps to finish.",
        tool_calls=None,
    )
    client = _FakeClient([_FakeResponse(call_tool), _FakeResponse(dressed_up)])
    provider = _make_main_provider(client)

    owl_registry = OwlRegistry.with_default_secretary()
    gate = ConsequentialActionGate(confirm_fn=lambda _name: True)
    services = _build_services(provider, owl_registry, tool_registry, bridge, consent_gate=gate)
    backend = _make_backend(backend_kind, services)

    delivered = await _execute_turn(
        "build a bridge to my email system", f"sess-p0-{backend_kind}", f"trace-p0-{backend_kind}", backend,
    )

    # OUTCOME 1 — the user sees the honest floor, NOT the lie.
    assert "built the full agentic bridge" not in delivered
    assert delivered.strip()
    assert "couldn" in delivered.lower() or "could not" in delivered.lower()

    # OUTCOME 2 (THE P0) — the persisted record must NOT carry the dressed-up draft,
    # so the dream worker can never promote the lie to a durable fact.
    staged = await bridge.list_staged()
    convo = [s for s in staged if s.source_type == "conversation"]
    for s in convo:
        assert "built the full agentic bridge" not in s.content, (
            "P0 FAIL: the dressed-up give-up draft was PERSISTED as a promotable "
            f"fact ({backend_kind} backend). content={s.content!r}"
        )
        assert "Assistant:" not in s.content, (
            "P0 FAIL: a floored turn persisted an assistant-text fact (must be "
            f"user-utterance only). content={s.content!r}"
        )
    # The user utterance MAY be persisted (failure-marked, untrusted) but never as a
    # confident self-authored success.
    for s in convo:
        assert s.trust != "self", (
            f"P0 FAIL: a floored turn was stamped trust='self' ({backend_kind}). {s.content!r}"
        )
    assert build.calls, "the consequential tool never ran — the failure never armed the floor."


# =========================================================================== #
# Control — a clean success persists the real delivered text trust='self'.
# =========================================================================== #


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_kind", _BACKENDS)
async def test_clean_success_persists_self_trust(
    backend_kind: str, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    bridge = SqliteMemoryBridge(db=tmp_db)
    # Register a tool so execute takes the complete_with_tools path (the fake client
    # implements create(), not stream()); the model answers DIRECTLY without using it.
    tool_registry = ToolRegistry()
    tool_registry.register(_ConsequentialTool("unused_tool", succeed=True))

    # The model answers directly on the first turn (no ACTION) → clean success.
    answer = _FakeMessage(content="The capital of France is Paris.", tool_calls=None)
    client = _FakeClient([_FakeResponse(answer)])
    provider = _make_main_provider(client)

    owl_registry = OwlRegistry.with_default_secretary()
    services = _build_services(provider, owl_registry, tool_registry, bridge)
    backend = _make_backend(backend_kind, services)

    delivered = await _execute_turn(
        "what is the capital of France?", f"sess-clean-{backend_kind}",
        f"trace-clean-{backend_kind}", backend,
    )
    assert "Paris" in delivered

    staged = await bridge.list_staged()
    convo = [s for s in staged if s.source_type == "conversation"]
    assert convo, f"clean success was not persisted ({backend_kind})."
    latest = convo[-1]
    assert "Assistant:" in latest.content
    assert "Paris" in latest.content
    assert latest.trust == "self", (
        f"clean success must be trust='self', got {latest.trust!r} ({backend_kind})."
    )


# NOTE: the tool-merge → trust='untrusted' path (consolidate merge branch +
# SP-2 carry + persist_turn read) is covered at the unit level in
# tests/pipeline/test_consolidate_merged_external.py and
# tests/pipeline/test_consolidate_trust.py. It is NOT reachable end-to-end
# because the execute step always produces a non-empty chunk (final_text or an
# honest floor), so consolidate's `tool_calls and not responses` merge branch
# never fires in the live pipeline — the unit tests drive it directly.
