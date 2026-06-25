"""Bounded-turn-guarantee gateway journeys — tool-spam spiral terminates (FR1/FR3/FR5).

Live-bug regression: a weak model spam-called a new tool every round on "hi" and
spiraled 11 minutes / to the 30-iteration hard cap. These two journeys prove the
turn now TERMINATES with a delivered reply far below that cap.

JOURNEY 1 — spiral-terminates (FR1/FR3)
  A REAL OpenAIProvider driven by a scripted fake client that ALWAYS returns a
  plain-text give-up draft (no tool call, no ACTION:).  A persistence-judge double
  installed on both the fast and local tiers always rules {"delivered": false} —
  maximum pressure toward nudging.  With the default safety backstop
  (DEFAULT_TURN_MAX_STEPS=20) and MAX_TURN_NUDGES=6:

    * Each round: on_iteration_complete fires (budget gate — no breach yet),
      then _enforce calls the persistence check → judge rules give-up →
      decide_nudge increments nudges_issued.
    * At nudges_issued >= MAX_TURN_NUDGES (6), decide_nudge returns None →
      _enforce returns None → the loop ACCEPTS the current draft and exits.
    * Provider round count <= MAX_TURN_NUDGES + 2 = 8.  The 30-cap was
      never reached — the NUDGE CEILING stopped the spiral.

JOURNEY 2 — happy-path unchanged (FR5)
  A scripted client that returns a real answer in 1 round; judge rules delivered.
  Asserts: 1 API call, the real answer delivered, no "stopped:" or "budget cap"
  in the reply.  Guards that the backstop and ceiling leave normal turns alone.

Driven through the REAL gateway / AsyncioBackend pipeline.  The ONLY mocks:
  * The OpenAI SDK client (scripted fake completions; drives the REAL OpenAIProvider
    loop including the nudge-ceiling logic in _enforce / decide_nudge).
  * The judge ModelProvider (scripted verdict on the fast and local tiers).
    It also handles the triage routing call (returns "secretary\\nstandard").
"""

from __future__ import annotations

from typing import Any, Literal

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.supervisor import MAX_TURN_NUDGES
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Minimal tool — required so execute._run_with_tools is taken (not stream path).
# Without at least one tool, ToolRegistry.all() is empty → _use_tools = False →
# streaming path → provider.stream() → wrong seam.
# ---------------------------------------------------------------------------


class _NopTool(Tool):
    """Read-severity no-op tool; its existence forces the tool-loop path."""

    @property
    def name(self) -> str:
        return "nop"

    @property
    def description(self) -> str:
        return "No-op probe tool."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="nop",
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok", duration_ms=0.0)


# ---------------------------------------------------------------------------
# Fake OpenAI SDK objects — same shape as test_self_heal_lying_judge.py
# ---------------------------------------------------------------------------


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
        self.model = "test-model"
        self.usage = None  # _record_usage_safe is guarded; None is safe


class _FakeCompletions:
    """Fake completions that track every create() call by recording messages."""

    def __init__(self, response_factory: Any) -> None:
        # response_factory(call_index: int) -> _FakeResponse
        self._factory = response_factory
        self.calls: list[list[dict[str, Any]]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append([dict(m) for m in kwargs.get("messages", [])])
        return self._factory(len(self.calls) - 1)


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, response_factory: Any) -> None:
        self.chat = _FakeChat(_FakeCompletions(response_factory))

    @property
    def calls(self) -> list[list[dict[str, Any]]]:
        return self.chat.completions.calls


def _make_openai_provider(client: _FakeClient) -> OpenAIProvider:
    """Build a REAL OpenAIProvider with the fake SDK client injected."""
    config = ProviderConfig(
        name="spiral-fake",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="test-model",
        tier="powerful",
    )
    p = OpenAIProvider(config, api_key="")
    p._client = client  # type: ignore[assignment]
    return p


# ---------------------------------------------------------------------------
# Dual-purpose judge/router provider double.
#
# Triage (SecretaryRouter) calls provider.complete() with a prompt that does
# NOT contain "AGENT DRAFT REPLY".  It expects "secretary" on line 1 (or any
# known owl name) and an optional intent class on line 2.  We return
# "secretary\nstandard" so routing passes cleanly and the tool-loop path is
# taken (intent_class == "standard", not "conversational").
#
# The persistence-check callback (build_persistence_check) calls
# provider.complete() with a prompt containing "AGENT DRAFT REPLY" (from
# persistence.py:_build_messages).  We return the configured delivered verdict.
# ---------------------------------------------------------------------------


class _DualJudgeProvider(ModelProvider):
    """Routes to secretary for triage; returns configured verdict for judge calls."""

    def __init__(self, *, judge_delivered: bool) -> None:
        self._delivered = judge_delivered
        self.judge_calls: list[str] = []

    @property
    def name(self) -> str:
        return "dual-judge"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        joined = "\n".join(m.content or "" for m in messages)
        if "AGENT DRAFT REPLY" in joined:
            # Persistence judge call — return the scripted verdict.
            self.judge_calls.append(joined[:80])
            verdict = "true" if self._delivered else "false"
            content = f'{{"delivered": {verdict}, "reason": "scripted-test"}}'
        else:
            # Triage / routing call — return the secretary owl name.
            # Line 2 = "standard" so the tool-loop path is taken.
            content = "secretary\nstandard"
        return CompletionResult(
            content=content,
            input_tokens=1,
            output_tokens=1,
            model="dual-judge",
            provider_name="dual-judge",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        if False:  # pragma: no cover
            yield ""


# ---------------------------------------------------------------------------
# Harness builder
# ---------------------------------------------------------------------------

_OWL = "secretary"


def _build_backend(
    main_client: _FakeClient,
    judge_delivered: bool,
) -> tuple[AsyncioBackend, GatewayScanner, _FakeClient, _DualJudgeProvider]:
    """Wire the full gateway — ONLY AI layer mocked."""
    provider = _make_openai_provider(main_client)
    judge = _DualJudgeProvider(judge_delivered=judge_delivered)

    preg = ProviderRegistry()
    # Main loop provider: by owl name (highest precedence in _select_tool_provider).
    preg.register_mock(_OWL, provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    # Judge tiers: fast (primary) and local (fallback) — same instance so the
    # ENTIRE cascade returns the scripted verdict.  Also serves the triage call.
    preg.register_mock("fast-judge", judge, tier="fast")
    preg.register_mock("local-judge", judge, tier="local")
    preg.register_mock("standard-judge", judge, tier="standard")

    owl_registry = OwlRegistry.with_default_secretary()
    # No explicit caps on the owl → default backstop: DEFAULT_TURN_MAX_STEPS=20.

    tool_registry = ToolRegistry()
    tool_registry.register(_NopTool())  # ensures _use_tools = True → complete_with_tools

    services = StepServices(
        provider_registry=preg,  # type: ignore[arg-type]
        tool_registry=tool_registry,
        owl_registry=owl_registry,
    )
    backend = AsyncioBackend(services=services)  # type: ignore[arg-type]
    scanner = GatewayScanner(owl_registry=owl_registry)
    return backend, scanner, main_client, judge


async def _run_turn(
    backend: AsyncioBackend,
    scanner: GatewayScanner,
    text: str = "hi",
) -> PipelineState:
    """Drive one IngressMessage through the REAL AsyncioBackend."""
    msg = IngressMessage(
        text=text,
        session_id="sess-bounded-turn",
        channel="cli",
        trace_id=f"trace-bounded-turn-{text[:8]}",
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
        interactive=False,  # default backstop: non-interactive (just stop + deliver)
    )
    return await backend.run(state)


# ---------------------------------------------------------------------------
# Fixture: disengage TestModeGuard so the real OpenAIProvider is allowed to run
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _live_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same guard disable as test_self_heal_lying_judge (the established pattern)."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)


# ===========================================================================
# JOURNEY 1 — spiral-terminates (FR1/FR3)
# ===========================================================================


async def test_tool_spam_spiral_terminates_via_nudge_ceiling() -> None:
    """A judge that always rules give-up cannot spiral forever.

    The nudge ceiling (MAX_TURN_NUDGES=6) fires inside decide_nudge →
    _enforce returns None → the loop accepts the current draft.  The turn
    delivers a NON-EMPTY reply in at most MAX_TURN_NUDGES+2 rounds (far below
    the 30-cap hard limit).

    FR1: the turn always terminates.
    FR3: it terminates via the nudge ceiling, not the 30-iteration hard cap.
    """
    # The scripted client always returns a plain-text give-up draft with no
    # ACTION: prefix and no tool_calls.  This puts the loop on the
    # "no action → draft final answer" branch where on_iteration_complete is
    # called (budget gate) then _enforce (persistence judge) per iteration.
    give_up_draft = "I cannot help with that request."

    def _always_giveup(call_index: int) -> _FakeResponse:
        return _FakeResponse(_FakeMessage(content=give_up_draft, tool_calls=None))

    client = _FakeClient(_always_giveup)

    backend, scanner, main_client, judge = _build_backend(
        main_client=client,
        judge_delivered=False,  # always rules give-up → maximum nudge pressure
    )

    final_state = await _run_turn(backend, scanner, text="hi")

    rounds = len(main_client.calls)
    delivered = "".join(c.content for c in final_state.responses)

    # OUTCOME 1 — the turn delivered a NON-EMPTY reply.
    assert final_state.responses, (
        f"BOUNDED-TURN FAIL (FR1): the spiral produced NO response — the turn hung "
        f"or delivered silence instead of terminating with a reply. "
        f"rounds={rounds}, errors={final_state.errors}"
    )
    assert delivered.strip(), (
        f"BOUNDED-TURN FAIL (FR1): the delivered response is empty. "
        f"rounds={rounds}, errors={final_state.errors}"
    )

    # OUTCOME 2 — the round count is <= MAX_TURN_NUDGES + 2.
    # With nudge ceiling=6 the loop exits when nudges_issued reaches 6, which
    # happens at round 7 (0-indexed rounds 0–6).  The +2 margin tolerates
    # the triage routing call and the budget callback ordering.
    assert rounds <= MAX_TURN_NUDGES + 2, (
        f"BOUNDED-TURN FAIL (FR3): the spiral ran {rounds} provider rounds — "
        f"the nudge ceiling did NOT stop it (expected <= {MAX_TURN_NUDGES + 2}). "
        f"Check that nudges_issued is tracked and MAX_TURN_NUDGES is enforced "
        f"in decide_nudge. errors={final_state.errors}"
    )

    # Sanity: the judge double WAS consulted at least once — confirms that
    # the persistence check path ran, not just the streaming/stream-error path.
    assert judge.judge_calls, (
        "BOUNDED-TURN FAIL: the judge double was never consulted for a delivery "
        "verdict — the persistence check path was not reached.  The test may be "
        "taking the streaming path instead of complete_with_tools (check that a "
        "tool is registered so _use_tools=True)."
    )


# ===========================================================================
# JOURNEY 2 — happy-path unchanged (FR5)
# ===========================================================================


async def test_normal_turn_unaffected_by_default_backstop() -> None:
    """A normal single-round answer is delivered unchanged by the default backstop.

    FR5: the backstop and nudge ceiling must NOT alter a turn that delivers
    normally on the first provider call.
    """
    _REAL_ANSWER = "Here is the information you asked for: everything is fine."

    def _real_answer(call_index: int) -> _FakeResponse:
        return _FakeResponse(_FakeMessage(content=_REAL_ANSWER, tool_calls=None))

    client = _FakeClient(_real_answer)

    backend, scanner, main_client, judge = _build_backend(
        main_client=client,
        judge_delivered=True,  # judge says "delivered" → no nudge → clean exit
    )

    final_state = await _run_turn(backend, scanner, text="hello, how are you?")

    delivered = "".join(c.content for c in final_state.responses)
    rounds = len(main_client.calls)

    # OUTCOME 1 — the answer was delivered.
    assert final_state.responses, (
        "HAPPY-PATH FAIL (FR5): no response chunks — normal turn produced silence."
    )
    assert _REAL_ANSWER in delivered, (
        f"HAPPY-PATH FAIL (FR5): the scripted answer was not in the delivered text. "
        f"delivered={delivered!r}"
    )

    # OUTCOME 2 — only 1 provider round (no nudge, no backstop intervention).
    assert rounds == 1, (
        f"HAPPY-PATH FAIL (FR5): expected 1 provider round but got {rounds}. "
        f"The backstop or nudge ceiling fired on a normal turn (it must not)."
    )

    # OUTCOME 3 — no truncation or budget marker in the delivered text.
    assert "stopped:" not in delivered.lower(), (
        f"HAPPY-PATH FAIL (FR5): 'stopped:' marker found in a normal-turn reply. "
        f"delivered={delivered!r}"
    )
    assert "budget cap" not in delivered.lower(), (
        f"HAPPY-PATH FAIL (FR5): 'budget cap' marker found in a normal-turn reply. "
        f"delivered={delivered!r}"
    )
