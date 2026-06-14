"""No-action-not-giveup journey — FR1 wiring + FR2/FR4 structural-veto control.

Two gates driven through the REAL AsyncioBackend pipeline:

  Test 1 — FR1 (headline proof): no-action standard turn, judge delivered=true
  → turn delivers the draft, NO persistence nudge.

  A routing provider classifies the turn as ``standard`` (NOT conversational) so
  the pipeline enters the REAL tool loop (``_run_with_tools``) and the judge runs
  via ``build_persistence_check``. The execute provider returns a substantive
  plain-text draft and ZERO tool calls (no ACTION: prefix, no native tool_calls).
  The judge double returns ``{"delivered": true, "reason": "directly answerable;
  on-point reply"}``, which is the verdict the reframed ``_build_messages`` prompt
  should produce for a no-action request.

  Assertions:
    • The draft is delivered (state.responses non-empty, contains the draft text).
    • NO persistence nudge occurred — the execute provider's
      ``client.chat.completions.create`` was called exactly ONCE.  A nudge injects
      ``PERSISTENCE_DIRECTIVE`` as a new user turn and re-calls ``create``; one
      call therefore proves the judge's ``delivered=true`` ended the loop (no spin).

  Test 2 — FR2/FR4 control (structural veto still intact after judge reframe):
  A consequential tool FAILS and the model drafts a dressed-up claim of success.
  The structural veto / ``surface_consequential_giveup_floor`` REPLACES the draft
  with the honest floor — the judge reframe must not have broken this path.

  Reuses the harness patterns from:
    • ``tests/journeys/test_self_heal_lying_judge.py`` (FakeClient + FakeCompletions
      + OpenAIProvider, nudge detection via call count + PERSISTENCE_DIRECTIVE in
      messages, LyingJudgeProvider dual-function disambiguation by "AGENT DRAFT REPLY")
    • ``tests/journeys/test_no_dressed_up_giveup_journey.py`` (ConsequentialTool,
      floor language assertions)

Mocks ONLY the AI provider/judge doubles. Everything else is real:
the AsyncioBackend pipeline, ToolRegistry, _dispatch, build_persistence_check,
decide_nudge, apply_structural_veto, surface_consequential_giveup_floor.
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
from stackowl.pipeline.persistence import PERSISTENCE_DIRECTIVE
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

# --------------------------------------------------------------------------- #
# Shared judge/router double — disambiguates routing calls from judge calls by
# the distinctive "AGENT DRAFT REPLY" marker in the persistence.py judge prompt.
# On routing calls: returns "secretary" (no intent-class suffix → standard).
# On judge calls: returns the delivered=true verdict the reframed prompt produces
# for a no-action request that received a real on-point reply.
# Installed on BOTH fast (primary) and local (fallback) judge tiers.
# --------------------------------------------------------------------------- #


class _DeliveredJudgeProvider(ModelProvider):
    """Returns ``delivered=true`` on judge prompts; ``secretary`` for routing.

    The routing reply has NO second line so SecretaryRouter stamps
    ``intent_class='standard'`` (the default), ensuring the tool loop runs and
    the judge is exercised — NOT the lean conversational path.
    """

    @property
    def name(self) -> str:
        return "delivered-judge-fake"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        joined = "\n".join(m.content for m in messages)
        content = (
            '{"delivered": true, "reason": "directly answerable; on-point reply"}'
            if "AGENT DRAFT REPLY" in joined
            else "secretary"
        )
        return CompletionResult(
            content=content,
            input_tokens=1,
            output_tokens=1,
            model="delivered-judge-fake",
            provider_name="delivered-judge-fake",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        yield "secretary"


# --------------------------------------------------------------------------- #
# Fake OpenAI SDK client — mirrors test_self_heal_lying_judge.py exactly.
# Drives a REAL OpenAIProvider; we inject it via provider._client.
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


# --------------------------------------------------------------------------- #
# Simple consequential tool for the FR2/FR4 control (Test 2).
# No capability_tag → no substitution sibling → genuine dressed-up give-up
# when it fails and the model claims success.
# --------------------------------------------------------------------------- #


class _ConsequentialTool(Tool):
    """A controllable consequential tool for the dressed-up-giveup control gate."""

    def __init__(self, name: str, *, severity: str, succeed: bool) -> None:
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
            capability_tag=None,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        if self._succeed:
            return ToolResult(success=True, output="ok", error=None, duration_ms=1.0)
        return ToolResult(
            success=False,
            output="",
            error="bridge construction failed: dependency unavailable",
            duration_ms=1.0,
        )


# --------------------------------------------------------------------------- #
# Service builder shared by both tests.
# --------------------------------------------------------------------------- #


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
    # Delivered judge on BOTH fast (primary) and local (fallback) tiers.
    judge = _DeliveredJudgeProvider()
    preg.register_mock("router", judge, tier="fast")
    preg.register_mock("local-judge", judge, tier="local")
    return StepServices(
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
        consent_gate=consent_gate,
    )


async def _execute_turn(
    text: str,
    session: str,
    trace: str,
    backend: AsyncioBackend,
) -> PipelineState:
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
    return await backend.run(state)


# =========================================================================== #
# Test 1 — FR1: no-action standard turn + no-tool draft + judge delivered=true
#           → delivered, NO nudge (1 provider call, PERSISTENCE_DIRECTIVE absent).
# =========================================================================== #

_SUBSTANTIVE_DRAFT = (
    "The capital of France is Paris. It has been the country's capital since "
    "the 10th century and is home to landmarks such as the Eiffel Tower and "
    "the Louvre. Is there anything else you would like to know?"
)


@pytest.mark.asyncio
async def test_no_action_standard_turn_not_nudged(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR1 wiring: a no-action request on the STANDARD path receives a real draft;
    the judge returns delivered=true; the turn ends with NO persistence nudge.

    The provider's ``create`` is called exactly ONCE — the loop does NOT spin.
    A second call would only happen if a nudge were injected (PERSISTENCE_DIRECTIVE
    appended as a user message and the loop continued), which would be the bug this
    test guards against.

    Routing uses secretary (no intent-class suffix → ``standard``) so the full tool
    loop runs, the judge is exercised, and the conversational bypass is NOT taken —
    this test proves the judge-reframe fix works on the real code path.
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # The execute provider: a single substantive plain-text draft, NO tool calls
    # (no ACTION: prefix, no native tool_calls). Zero tools contacted.
    draft_msg = _FakeMessage(content=_SUBSTANTIVE_DRAFT, tool_calls=None)
    client = _FakeClient([_FakeResponse(draft_msg)])
    provider = _make_main_provider(client)

    owl_registry = OwlRegistry.with_default_secretary()
    # A trivial read tool is registered so ``_use_tools=True`` (standard path).
    # It will NOT be called — the model returns a plain answer on the first turn.
    # Without any registered tool the pipeline would take the plain-stream path,
    # bypassing the tool loop and therefore bypassing the judge entirely.
    class _TrivialTool(Tool):
        @property
        def name(self) -> str:
            return "noop_read"

        @property
        def description(self) -> str:
            return "A no-op read tool."

        @property
        def parameters(self) -> dict[str, object]:
            return {"type": "object", "properties": {}}

        async def execute(self, **kwargs: object) -> ToolResult:
            return ToolResult(success=True, output="", error=None, duration_ms=0.0)

    tool_registry = ToolRegistry()
    tool_registry.register(_TrivialTool())

    services = _build_services(provider, owl_registry, tool_registry)
    backend = AsyncioBackend(services=services)

    final_state = await _execute_turn(
        "What is the capital of France?",
        "sess-no-action-no-nudge",
        "trace-no-action-1",
        backend,
    )
    delivered = "".join(c.content for c in final_state.responses)

    # ==========================================================================
    # OUTCOME 1 — draft is delivered (non-empty, contains the draft text).
    # ==========================================================================
    assert delivered.strip(), (
        "FR1 FAIL: the delivered response is empty — the draft was not delivered. "
        f"Responses: {final_state.responses!r}"
    )
    assert _SUBSTANTIVE_DRAFT in delivered, (
        "FR1 FAIL: the delivered response does not contain the expected draft. "
        f"Delivered: {delivered!r}"
    )

    # ==========================================================================
    # OUTCOME 2 — NO persistence nudge: exactly 1 provider call.
    # A nudge would inject PERSISTENCE_DIRECTIVE as a user turn and call create()
    # again; one call proves delivered=true ended the loop with no spin.
    # ==========================================================================
    n_calls = len(client.chat.completions.calls)
    assert n_calls == 1, (
        "FR1 FAIL: expected exactly 1 provider call (no nudge), got "
        f"{n_calls}. The judge delivered=true verdict did NOT end the loop — "
        "the turn spun unnecessarily. "
        f"Calls recorded: {n_calls}"
    )

    # ==========================================================================
    # OUTCOME 3 (belt-and-braces) — PERSISTENCE_DIRECTIVE is absent from all
    # provider calls. This is the message-content proof that no nudge was injected
    # (mirrors the lying-judge gate's third assertion, inverted).
    # ==========================================================================
    for i, call_msgs in enumerate(client.chat.completions.calls):
        for m in call_msgs:
            assert m.get("content") != PERSISTENCE_DIRECTIVE, (
                f"FR1 FAIL: PERSISTENCE_DIRECTIVE was injected into call {i} — "
                "a nudge occurred despite delivered=true verdict. "
                f"Message content: {m.get('content')!r}"
            )


# =========================================================================== #
# Test 2 — FR2/FR4 control: consequential fail + dressed-up claim → honest floor.
# Structural veto still fires after the judge reframe (regression guard).
# =========================================================================== #


@pytest.mark.asyncio
async def test_consequential_fail_still_floored_post_reframe(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR2/FR4 control: the judge reframe must NOT have broken the structural veto path.

    A consequential tool FAILS; the scripted provider drafts a dressed-up claim
    of success. No substitution sibling exists. The honest floor must REPLACE the
    dressed-up claim before delivery.

    This is a regression guard: the judge double returns delivered=true (fail-open)
    so the floor fires purely from the ledger + giveup_floor, NOT from the judge —
    proving the structural veto is independent of the judge reframe.

    ASSERT:
      FR4 — the dressed-up claim is GONE from delivered text; the honest floor
             is present (non-empty; contains "couldn" or "could not").
      FR2 — the structural veto fires post-reframe (the reframe did not disable it).
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # The consequential tool that FAILS — no capability_tag → no sibling.
    build_bridge = _ConsequentialTool(
        "build_agentic_bridge",
        severity="consequential",
        succeed=False,
    )
    tool_registry = ToolRegistry()
    tool_registry.register(build_bridge)

    # Iter 0: call the failing consequential tool.
    # Iter 1: model produces a dressed-up claim — the floor must replace it.
    call_tool = _FakeMessage(
        content=(
            "ACTION: build_agentic_bridge\n```json\n"
            '{"target": "email_delivery_system"}\n```'
        ),
        tool_calls=None,
    )
    dressed_up = _FakeMessage(
        content=(
            "I have built the full agentic bridge for you. "
            "Here are the steps you can follow to complete the setup."
        ),
        tool_calls=None,
    )
    client = _FakeClient([_FakeResponse(call_tool), _FakeResponse(dressed_up)])
    provider = _make_main_provider(client)

    owl_registry = OwlRegistry.with_default_secretary()
    gate = ConsequentialActionGate(confirm_fn=lambda _name: True)
    services = _build_services(
        provider, owl_registry, tool_registry, consent_gate=gate
    )
    backend = AsyncioBackend(services=services)

    final_state = await _execute_turn(
        "build an agentic bridge to my email delivery system",
        "sess-control-floor",
        "trace-control-floor-1",
        backend,
    )
    delivered = "".join(c.content for c in final_state.responses)

    # ==========================================================================
    # FR4 — the dressed-up claim must NOT appear in the delivered text.
    # ==========================================================================
    assert "built the full agentic bridge" not in delivered, (
        "FR4 CONTROL FAIL: the dressed-up give-up claim survived delivery — "
        "surface_consequential_giveup_floor did not fire after the judge reframe. "
        f"Delivered: {delivered!r}"
    )
    assert "here are the steps" not in delivered.lower(), (
        "FR4 CONTROL FAIL: the manual-steps hand-off survived delivery — "
        "the floor did not replace the excuse after the judge reframe. "
        f"Delivered: {delivered!r}"
    )

    # ==========================================================================
    # FR2 — the honest floor IS present (non-empty; honest language).
    # ==========================================================================
    assert delivered.strip(), (
        "FR2 CONTROL FAIL: delivered text is empty — honest floor must be non-empty. "
        f"Delivered: {delivered!r}"
    )
    floor_honest = "couldn" in delivered.lower() or "could not" in delivered.lower()
    assert floor_honest, (
        "FR2 CONTROL FAIL: honest floor language ('couldn' / 'could not') not found. "
        "The floor should name the failure honestly, post-reframe. "
        f"Delivered: {delivered!r}"
    )

    # Sanity: the consequential tool DID run and fail.
    assert build_bridge.calls, (
        "FR2 CONTROL sanity fail: consequential tool never ran — "
        "the failure that arms the floor predicate never occurred."
    )
