"""Recovery Explainability Journey — end-to-end proof of pillar ④ (substitution surface).

When a capability substitution recovers a turn, the user-visible response must
contain a deterministic explanation line — "ℹ️ '{failed}' was unavailable, so I
used '{recovered_via}' to complete this." — appended AFTER the real answer.

Drives the REAL AsyncioBackend pipeline + REAL substitution actuator; mocks ONLY
the AI provider. Mirrors the harness from
``tests/journeys/test_self_heal_substitution.py`` with changed assertions.

FR coverage:
  FR1 — happy-path: substitution fires → recovery line present in delivered text.
  FR2 — no-substitution: plain success with no tool failure → recovery line absent.
  FR3 — floored turn: substitution recorded but turn produces no real answer →
         recovery line absent (honesty guard suppresses the annotation).
  FR4 — broad log: ``[recovery] turn summary`` log record emitted per turn with
         events, captured via caplog on the ``stackowl.engine`` logger.
"""

from __future__ import annotations

import logging
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
# Shared capability-class tool — mirrors _CapabilityTool from
# test_self_heal_substitution.py EXACTLY (same adapter names so the declarative
# web_knowledge adapters apply).
# --------------------------------------------------------------------------- #

_WEB_TAG = "web_knowledge"


class _CapabilityTool(Tool):
    """A web_knowledge-class tool with controllable severity + success."""

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


# --------------------------------------------------------------------------- #
# Router / judge provider — mirrors test_self_heal_substitution.py EXACTLY.
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
# Fake OpenAI SDK client — mirrors test_self_heal_substitution.py EXACTLY.
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


def _run_state(text: str, session: str, trace: str) -> tuple[GatewayScanner, IngressMessage]:
    scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())
    msg = IngressMessage(
        text=text,
        session_id=session,
        channel="cli",
        trace_id=trace,
    )
    return scanner, msg


async def _execute_turn(
    text: str,
    session: str,
    trace: str,
    backend: AsyncioBackend,
) -> str:
    scanner, msg = _run_state(text, session, trace)
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
    return "".join(c.content for c in final_state.responses)


# =========================================================================== #
# FR1 — happy-path: substitution fires, recovery line present (+ FR4 log).
# =========================================================================== #


@pytest.mark.asyncio
async def test_recovery_line_present_after_substitution(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR1: When browser_browse (consequential, failing) is substituted by
    web_search (read, succeeding), the delivered user response must contain BOTH
    the model's final answer AND the recovery explanation line with the failed
    tool's name, the sibling's name, and the ℹ️ marker.

    FR4 (broad log): The ``[recovery] turn summary`` log record must be emitted
    on the ``stackowl.engine`` logger during the turn.
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    sibling_payload = "WEATHER: sunny 24C"

    # browser_browse (consequential) = the failing primary.
    # web_search (read) = the succeeding sibling that triggers the recovery.
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

    # Iter 0: call the failing primary → _try_substitute picks web_search →
    #         runs it → observation carries sibling's data.
    # Iter 1: model produces a final answer based on the sibling's data.
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
    gate = ConsequentialActionGate(confirm_fn=lambda _name: True)
    services = _build_services(provider, owl_registry, tool_registry, consent_gate=gate)
    backend = AsyncioBackend(services=services)

    with caplog.at_level(logging.INFO, logger="stackowl.engine"):
        delivered = await _execute_turn(
            "what's the weather today?",
            "sess-recovery-explain-fr1",
            "trace-recovery-explain-1",
            backend,
        )

    # ===================================================================
    # FR1 OUTCOME 1 — the model's final answer is present.
    # ===================================================================
    assert "sunny 24C" in delivered, (
        f"FR1 FAIL: final answer ('sunny 24C') missing from delivered text. "
        f"Got: {delivered!r}"
    )

    # ===================================================================
    # FR1 OUTCOME 2 — the recovery explanation line is present with BOTH
    # tool names and the ℹ️ marker (per self_heal_recovery_note template).
    # ===================================================================
    assert "browser_browse" in delivered, (
        f"FR1 FAIL: failed tool name ('browser_browse') missing from recovery line. "
        f"Got: {delivered!r}"
    )
    assert "web_search" in delivered, (
        f"FR1 FAIL: sibling tool name ('web_search') missing from recovery line. "
        f"Got: {delivered!r}"
    )
    assert "ℹ️" in delivered, (
        f"FR1 FAIL: ℹ️ marker missing from recovery line. Got: {delivered!r}"
    )

    # ===================================================================
    # FR4 — the [recovery] turn summary log record was emitted.
    # ===================================================================
    recovery_log_records = [
        r for r in caplog.records if "[recovery] turn summary" in r.getMessage()
    ]
    assert recovery_log_records, (
        f"FR4 FAIL: '[recovery] turn summary' log record not found in caplog. "
        f"Records: {[r.getMessage() for r in caplog.records]}"
    )


# =========================================================================== #
# FR2 — no-substitution: no tool failure → recovery line absent.
# =========================================================================== #


@pytest.mark.asyncio
async def test_no_recovery_line_without_substitution(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR2: When the provider returns a direct answer with no tool failure (no
    substitution), the delivered response must NOT contain the recovery line or
    the ℹ️ marker — the explanation is only appended for genuine recoveries."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # Both tools registered but only the sibling (read) is available — however,
    # the scripted provider never calls ANY tool; it goes straight to a final answer.
    sibling = _CapabilityTool(
        "web_search",
        severity="read",
        capability_tag=_WEB_TAG,
        output="SEARCH_OK",
        succeed=True,
        params={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    tool_registry = ToolRegistry()
    tool_registry.register(sibling)

    # Single provider call: return a direct final answer with no tool invocation.
    direct_answer = _FakeMessage(
        content="The capital of France is Paris.",
        tool_calls=None,
    )
    client = _FakeClient([_FakeResponse(direct_answer)])
    provider = _make_main_provider(client)

    owl_registry = OwlRegistry.with_default_secretary()
    gate = ConsequentialActionGate(confirm_fn=lambda _name: True)
    services = _build_services(provider, owl_registry, tool_registry, consent_gate=gate)
    backend = AsyncioBackend(services=services)

    delivered = await _execute_turn(
        "what is the capital of France?",
        "sess-recovery-explain-fr2",
        "trace-recovery-explain-2",
        backend,
    )

    # ===================================================================
    # FR2 OUTCOME 1 — the answer is present (sanity check).
    # ===================================================================
    assert "Paris" in delivered, (
        f"FR2 FAIL: expected answer ('Paris') missing from delivered text. "
        f"Got: {delivered!r}"
    )

    # ===================================================================
    # FR2 OUTCOME 2 — no recovery line (no substitution occurred).
    # ===================================================================
    assert "ℹ️" not in delivered, (
        f"FR2 HONESTY FAIL: ℹ️ marker appeared even though no substitution occurred. "
        f"Got: {delivered!r}"
    )
    assert "was unavailable, so I used" not in delivered, (
        f"FR2 HONESTY FAIL: recovery template text appeared with no substitution. "
        f"Got: {delivered!r}"
    )


# =========================================================================== #
# FR3 — substitution recorded but turn floors → recovery line absent.
# =========================================================================== #


@pytest.mark.asyncio
async def test_no_recovery_line_on_failed_turn(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR3 (honesty guard): A substitution IS recorded (browser_browse fails →
    web_search recovers), but then the provider RAISES on the second call,
    causing the execute step to critically fail with no usable response. The
    delivered text must NOT contain the recovery explanation line — the floor /
    apology explains the failure; appending a recovery note onto a failed turn
    would be a false success claim.

    The guard in ``surface_recovery`` that enforces this is:
      ``has_real_answer = any(c.content.strip() and not c.is_floor for c in responses)``
    If this test fails (ℹ️ present on a failed turn), that is a REAL honesty
    defect — BLOCKED, not weakened.
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    sibling_payload = "WEATHER: sunny 24C"

    # Same tool pairing as FR1: primary fails, sibling succeeds → substitution
    # records the event (recovery_context.record_recovery is called).
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

    # Iter 0: call the failing primary → substitution fires → sibling succeeds →
    #         recovery is RECORDED in recovery_context.
    # Iter 1: provider RAISES — simulates a crash AFTER the substitution was
    #         recorded. The execute step fails with no usable response, triggering
    #         the floor / apology path. The recovery note must NOT be appended.
    call_primary = _FakeMessage(
        content=(
            "ACTION: browser_browse\n```json\n"
            '{"task": "weather today in Paris"}\n```'
        ),
        tool_calls=None,
    )

    # We need the second call to raise. Subclass to override behavior on second call.
    class _CrashOnSecondCallCompletions(_FakeCompletions):
        async def create(self, **kwargs: Any) -> _FakeResponse:
            self.calls.append([dict(m) for m in kwargs["messages"]])
            if self._i == 0:
                resp = self._responses[0]
                self._i += 1
                return resp
            # Second call: raise to simulate post-substitution provider crash.
            raise RuntimeError("provider crashed after substitution (simulated outage)")

    class _CrashOnSecondClient:
        def __init__(self) -> None:
            completions = _CrashOnSecondCallCompletions([_FakeResponse(call_primary)])
            self.chat = _FakeChat(completions)

    crash_client = _CrashOnSecondClient()
    provider = _make_main_provider(crash_client)  # type: ignore[arg-type]

    owl_registry = OwlRegistry.with_default_secretary()
    gate = ConsequentialActionGate(confirm_fn=lambda _name: True)
    services = _build_services(provider, owl_registry, tool_registry, consent_gate=gate)
    backend = AsyncioBackend(services=services)

    delivered = await _execute_turn(
        "what's the weather today?",
        "sess-recovery-explain-fr3",
        "trace-recovery-explain-3",
        backend,
    )

    # ===================================================================
    # FR3 HONESTY GATE — the recovery line must NOT be present on a failed
    # turn, even though the substitution was recorded this turn.
    # Leaking the note onto a failed turn is a false success claim.
    # ===================================================================
    assert "ℹ️" not in delivered, (
        f"FR3 HONESTY DEFECT: ℹ️ recovery marker appeared on a turn that "
        f"critically failed after the substitution was recorded. "
        f"The recovery note must only annotate real (non-floor) answers. "
        f"Got: {delivered!r}"
    )
    assert "was unavailable, so I used" not in delivered, (
        f"FR3 HONESTY DEFECT: recovery template text appeared on a critically "
        f"failed turn. Got: {delivered!r}"
    )
