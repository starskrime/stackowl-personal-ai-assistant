"""No-dressed-up-giveup journey — consequential fail → honest floor, not the excuse.

End-to-end gateway regression for the "give-up dressed as delivery" fix.

When a CONSEQUENTIAL tool is attempted-and-FAILED with NO consequential success
(and no substitution sibling bridges the gap), ``surface_consequential_giveup_floor``
REPLACES the model's draft with the deterministic honest floor BEFORE delivery.
The dressed-up claim the model drafted must NEVER reach the user — only the honest
floor does.

Two gates, driven through the REAL AsyncioBackend pipeline:

  Test 1 — happy-path / FR1/FR3/FR4 (the headline proof). A consequential tool
  FAILS; the scripted provider drafts a dressed-up excuse ("I have built the full
  agentic bridge for you. Here are the steps..."). No substitution sibling exists.
  ASSERT: the dressed-up claim is GONE from delivered text; the honest floor is
  present (non-empty; contains "couldn't" or "could not"); FR3 directive was
  injected (best-effort via caplog); FR4 floor is judge-independent.

  Test 2 — control / FR2 (no false-positive). A consequential tool SUCCEEDS +
  the provider drafts a normal "done" reply. ASSERT: the draft is delivered
  unchanged (the floor-replace must NOT fire on a successful consequential action).

Mirrors the harness from ``tests/journeys/test_self_heal_substitution.py``
(scripted provider + real AsyncioBackend + real ToolRegistry + tool_dispatcher).
Mocks ONLY the AI provider.
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
# Simple consequential tool: controllable severity + success flag.
# No capability_tag / no substitution sibling → a genuine give-up when it fails.
# --------------------------------------------------------------------------- #


class _ConsequentialTool(Tool):
    """A tool with a controllable severity + outcome for the dressed-up-giveup gate."""

    def __init__(
        self,
        name: str,
        *,
        severity: str,
        succeed: bool,
    ) -> None:
        self._name = name
        self._severity = severity
        self._succeed = succeed
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"agentic bridge tool: {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {"target": {"type": "string"}},
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name,
            description=self.description,
            parameters=self.parameters,
            action_severity=self._severity,  # type: ignore[arg-type]
            capability_tag=None,  # no sibling → no substitution
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        if self._succeed:
            return ToolResult(success=True, output="agentic_bridge_ok", error=None, duration_ms=1.0)
        return ToolResult(
            success=False,
            output="",
            error="agentic bridge construction failed: dependency unavailable",
            duration_ms=1.0,
        )


# --------------------------------------------------------------------------- #
# Router / judge provider — same shape as test_self_heal_substitution.py.
# Returns {delivered: true} on judge prompts (fail-open, judge-independent test).
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
    preg.register_mock("standard-judge", router, tier="standard")
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
# Test 1 — FR1/FR3/FR4 (the headline proof): consequential fail → honest floor.
# =========================================================================== #


@pytest.mark.asyncio
async def test_dressed_up_giveup_replaced_with_honest_floor(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR4 (load-bearing): A consequential tool FAILS; the model drafts a dressed-up
    excuse claiming to have built an agentic bridge. No substitution sibling exists.
    ``surface_consequential_giveup_floor`` MUST replace the draft with the honest floor.

    ASSERT:
      FR4 — the dressed-up claim is GONE from delivered text; the honest floor is
             present (non-empty; contains 'couldn' or 'could not').
      FR3 — the capability-gap directive log appears in the stackowl.engine logger
             (best-effort: the structural veto fires this on the consequential signal).

    This is judge-independent: the judge fake returns {delivered: true} (fail-open),
    so the floor fires purely from the ledger + giveup_floor, NOT from a judge ruling.
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # The consequential tool that FAILS — no capability_tag → no sibling → genuine give-up.
    build_bridge = _ConsequentialTool(
        "build_agentic_bridge",
        severity="consequential",
        succeed=False,
    )
    tool_registry = ToolRegistry()
    tool_registry.register(build_bridge)

    # Iter 0: call the failing consequential tool.
    # Iter 1: model produces the DRESSED-UP excuse — this is what the floor must replace.
    call_tool = _FakeMessage(
        content=(
            "ACTION: build_agentic_bridge\n```json\n"
            '{"target": "email_delivery_system"}\n```'
        ),
        tool_calls=None,
    )
    dressed_up_draft = _FakeMessage(
        content=(
            "I have built the full agentic bridge for you. "
            "Here are the steps you can follow to complete the setup."
        ),
        tool_calls=None,
    )
    client = _FakeClient([_FakeResponse(call_tool), _FakeResponse(dressed_up_draft)])
    provider = _make_main_provider(client)

    owl_registry = OwlRegistry.with_default_secretary()
    # Auto-approving gate: consequential tool is approved to run (and fail),
    # so the ledger records the failure and giveup_floor fires.
    gate = ConsequentialActionGate(confirm_fn=lambda _name: True)
    services = _build_services(provider, owl_registry, tool_registry, consent_gate=gate)
    backend = AsyncioBackend(services=services)

    with caplog.at_level(logging.INFO, logger="stackowl.engine"):
        delivered = await _execute_turn(
            "build an agentic bridge to my email delivery system",
            "sess-giveup-floor-fr1",
            "trace-giveup-floor-1",
            backend,
        )

    # ==========================================================================
    # FR4 GATE 1 — the dressed-up claim must NOT appear in the delivered text.
    # This is the live-email-excuse bug regression: the excuse that the model
    # drafted must be replaced, not passed through.
    # ==========================================================================
    assert "built the full agentic bridge" not in delivered, (
        "MERGE-GATE FAIL (FR4): the dressed-up give-up claim survived delivery — "
        "surface_consequential_giveup_floor did not fire end-to-end. "
        f"Delivered: {delivered!r}"
    )
    assert "here are the steps" not in delivered.lower(), (
        "MERGE-GATE FAIL (FR4): the manual-steps hand-off text survived delivery — "
        "the floor did not replace the excuse. "
        f"Delivered: {delivered!r}"
    )

    # ==========================================================================
    # FR4 GATE 2 — the honest floor IS present (non-empty; contains honest language).
    # The floor template from localize.py: "I couldn't fully complete this: ..."
    # ==========================================================================
    assert delivered.strip(), (
        "MERGE-GATE FAIL (FR4): delivered text is empty — floor must produce "
        "a non-empty honest response. "
        f"Delivered: {delivered!r}"
    )
    floor_honest = "couldn" in delivered.lower() or "could not" in delivered.lower()
    assert floor_honest, (
        "MERGE-GATE FAIL (FR4): honest floor language ('couldn' / 'could not') "
        "not found in delivered text. The floor should name the failure honestly. "
        f"Delivered: {delivered!r}"
    )

    # ==========================================================================
    # FR3 GATE (best-effort) — capability-gap directive was logged.
    # The structural veto in supervisor.py emits this log when the consequential
    # signal fires (before or during the loop via _enforce).
    # ==========================================================================
    directive_records = [
        r
        for r in caplog.records
        if "capability-gap directive" in r.getMessage()
        or "capability_gap" in r.getMessage()
        or "CAPABILITY_GAP" in r.getMessage()
    ]
    if not directive_records:
        # Surface the caplog for diagnostics — but do NOT fail the test on FR3
        # because the floor (FR4) is the load-bearing assertion; FR3 is best-effort.
        import warnings

        all_msgs = [r.getMessage() for r in caplog.records if r.name.startswith("stackowl")]
        warnings.warn(
            f"FR3 (best-effort): capability-gap directive log not found. "
            f"Engine logs: {all_msgs!r}",
            stacklevel=1,
        )

    # Sanity: the consequential tool DID run and fail (proving the failure was real).
    assert build_bridge.calls, (
        "MERGE-GATE FAIL: the consequential tool never ran — "
        "the failure that arms the floor predicate never occurred."
    )


# =========================================================================== #
# Test 2 — FR2 (control / no false positive): consequential SUCCESS → draft unchanged.
# =========================================================================== #


@pytest.mark.asyncio
async def test_no_floor_replace_on_consequential_success(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR2: A consequential tool SUCCEEDS and the provider returns a normal 'done'
    draft. The floor-replace must NOT fire: the draft must be delivered unchanged.

    This guards against false positives: surface_consequential_giveup_floor only
    fires when there is an UNACHIEVED consequential outcome — never on success.
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # The consequential tool that SUCCEEDS this time.
    build_bridge = _ConsequentialTool(
        "build_agentic_bridge",
        severity="consequential",
        succeed=True,
    )
    tool_registry = ToolRegistry()
    tool_registry.register(build_bridge)

    # Iter 0: call the consequential tool (succeeds).
    # Iter 1: model produces a normal "done" draft — this must be preserved.
    call_tool = _FakeMessage(
        content=(
            "ACTION: build_agentic_bridge\n```json\n"
            '{"target": "email_delivery_system"}\n```'
        ),
        tool_calls=None,
    )
    success_draft = _FakeMessage(
        content="I have successfully built the agentic bridge. The system is now connected.",
        tool_calls=None,
    )
    client = _FakeClient([_FakeResponse(call_tool), _FakeResponse(success_draft)])
    provider = _make_main_provider(client)

    owl_registry = OwlRegistry.with_default_secretary()
    gate = ConsequentialActionGate(confirm_fn=lambda _name: True)
    services = _build_services(provider, owl_registry, tool_registry, consent_gate=gate)
    backend = AsyncioBackend(services=services)

    delivered = await _execute_turn(
        "build an agentic bridge to my email delivery system",
        "sess-giveup-floor-fr2",
        "trace-giveup-floor-2",
        backend,
    )

    # ==========================================================================
    # FR2 OUTCOME — the success draft is delivered unchanged (no floor replace).
    # ==========================================================================
    assert "successfully built the agentic bridge" in delivered, (
        "FR2 FAIL (false-positive guard): the consequential-SUCCESS draft was NOT "
        "delivered — surface_consequential_giveup_floor fired on a success, which "
        "is a false positive. The draft must be preserved on success. "
        f"Delivered: {delivered!r}"
    )

    # The honest floor language must NOT appear (it would indicate a false positive).
    assert "couldn" not in delivered.lower() and "could not" not in delivered.lower(), (
        "FR2 FAIL (false-positive guard): floor language ('couldn' / 'could not') "
        "appeared even though the consequential tool SUCCEEDED. "
        f"Delivered: {delivered!r}"
    )

    # Sanity: the tool DID run and succeed.
    assert build_bridge.calls, (
        "FR2 sanity fail: consequential tool never ran — the success never occurred."
    )
