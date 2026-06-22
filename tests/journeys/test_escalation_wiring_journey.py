"""Integration journey — a weak FAST tier that leaks/fails escalates to a stronger
tier that succeeds, driven end-to-end through the REAL AsyncioBackend (mocks ONLY
the AI client).

Three behaviours, all through the real pipeline (scanner → triage → classify →
execute tool loop → deliver):

  * ESCALATE — the fast tier persistently leaks an unparsed tool call; the turn
    escalates to a stronger tier and the DELIVERED text is the real result, never a
    raw ``{"action": ...}`` / ``ACTION:`` block, never silence.
  * ALL TIERS FAIL — every tier leaks; the user gets an honest floor, never raw JSON.
  * PINNED — an owl-named provider choice does NOT escalate (single tier); the
    stronger tier is never consulted.

Mirrors the harness in tests/journeys/test_tool_call_leak_journey.py.
"""

from __future__ import annotations

from collections.abc import Callable
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
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry

# The leaked tool call a weak model emits as its "answer" (bare JSON, the exact
# shape the user reported — a skill_manage create call written as text).
_LEAK = '{"action": "create", "name": "guardrail-check", "content": "---nname: x"}'
_STRONG_ANSWER = "Here is your finished guardrail skill, ready to use."


class _NoopTool(Tool):
    """A registered tool so the turn enters the agentic loop."""

    @property
    def name(self) -> str:
        return "skill_manage"

    @property
    def description(self) -> str:
        return "create or manage a skill"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"action": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name, description=self.description, parameters=self.parameters,
            action_severity="write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok", error=None, duration_ms=1.0)


# -- fake OpenAI client (serves BOTH .complete [routing/judge] and the tool loop) -- #


class _Usage:
    prompt_tokens = 1
    completion_tokens = 1


class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content
        self.tool_calls = None


class _Choice:
    def __init__(self, msg: _Msg) -> None:
        self.message = msg


class _Resp:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(_Msg(content))]
        self.model = "fake-model"
        self.usage = _Usage()


class _Completions:
    def __init__(self, responder: Callable[[dict[str, Any], bool], str], spy: list[bool]) -> None:
        self._responder = responder
        self._spy = spy

    async def create(self, **kwargs: Any) -> _Resp:
        used_tools = bool(kwargs.get("tools"))
        self._spy.append(used_tools)
        return _Resp(self._responder(kwargs, used_tools))


class _Chat:
    def __init__(self, completions: _Completions) -> None:
        self.completions = completions


class _Client:
    def __init__(self, responder: Callable[[dict[str, Any], bool], str]) -> None:
        # spy: one bool per create() call — True when the tool loop drove it.
        self.tool_loop_calls: list[bool] = []
        self.chat = _Chat(_Completions(responder, self.tool_loop_calls))


def _routing_or_judge(kwargs: dict[str, Any]) -> str:
    """Reply for a non-tool .complete() call: route to secretary, judge delivered."""
    joined = "\n".join((m.get("content") or "") for m in kwargs.get("messages", []))
    return '{"delivered": true, "reason": "ok"}' if "AGENT DRAFT REPLY" in joined else "secretary"


def _leak_responder(kwargs: dict[str, Any], used_tools: bool) -> str:
    return _LEAK if used_tools else _routing_or_judge(kwargs)


def _strong_responder(kwargs: dict[str, Any], used_tools: bool) -> str:
    return _STRONG_ANSWER if used_tools else _routing_or_judge(kwargs)


def _provider(name: str, responder: Callable[[dict[str, Any], bool], str], tier: str) -> OpenAIProvider:
    config = ProviderConfig(
        name=name, protocol="openai", base_url="http://localhost:11434/v1",
        default_model="gemma4:e4b", tier=tier,
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = _Client(responder)  # type: ignore[assignment]
    return provider


async def _execute_turn(text: str, backend: AsyncioBackend, *, session: str, trace: str) -> str:
    scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())
    msg = IngressMessage(text=text, session_id=session, channel="cli", trace_id=trace)
    decision = scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start", interactive=True,
    )
    final_state = await backend.run(state)
    return "".join(c.content for c in final_state.responses)


def _assert_no_raw_tool_call(delivered: str) -> None:
    assert '"action"' not in delivered, f"leaked tool-call JSON reached the user: {delivered!r}"
    assert "ACTION:" not in delivered, f"leaked ACTION block reached the user: {delivered!r}"
    assert delivered.strip(), "the user was left with SILENCE instead of an honest answer"


@pytest.mark.asyncio
async def test_weak_fast_tier_leak_escalates_to_stronger_tier(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    tool_registry = ToolRegistry()
    tool_registry.register(_NoopTool())

    fast = _provider("weak-fast", _leak_responder, "fast")
    strong = _provider("strong", _strong_responder, "standard")
    preg = ProviderRegistry()
    preg.register_mock("weak-fast", fast, tier="fast")
    preg.register_mock("strong", strong, tier="standard")
    preg.register_mock("strong-powerful", strong, tier="powerful")
    services = StepServices(
        provider_registry=preg,
        owl_registry=OwlRegistry.with_default_secretary(),
        tool_registry=tool_registry,
    )
    backend = AsyncioBackend(services=services)

    delivered = await _execute_turn(
        "create a guardrail skill for me", backend, session="sess-esc", trace="trace-esc",
    )

    _assert_no_raw_tool_call(delivered)
    # The real result from the stronger tier owns the turn.
    assert _STRONG_ANSWER in delivered, f"escalation did not deliver the strong answer: {delivered!r}"
    # The turn STARTED at the weak fast tier (its tool loop ran and leaked) — this is
    # only true when fast→escalate is wired; today execute would go straight to the
    # powerful ceiling and the fast tier would never run.
    assert any(fast._client.tool_loop_calls), (  # type: ignore[attr-defined]
        "the weak fast tier never ran — the turn did not start at 'fast' and escalate"
    )
    # And the stronger tier's tool loop actually ran (escalation reached it).
    assert any(strong._client.tool_loop_calls), "the strong tier's tool loop never ran"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_all_tiers_fail_delivers_honest_floor_not_raw_json(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    tool_registry = ToolRegistry()
    tool_registry.register(_NoopTool())

    leaker = _provider("weak", _leak_responder, "fast")
    preg = ProviderRegistry()
    preg.register_mock("weak-fast", leaker, tier="fast")
    preg.register_mock("weak-standard", leaker, tier="standard")
    preg.register_mock("weak-powerful", leaker, tier="powerful")
    services = StepServices(
        provider_registry=preg,
        owl_registry=OwlRegistry.with_default_secretary(),
        tool_registry=tool_registry,
    )
    backend = AsyncioBackend(services=services)

    delivered = await _execute_turn(
        "create a guardrail skill for me", backend, session="sess-allfail", trace="trace-allfail",
    )

    # Top tier also leaks → honest floor, never the raw tool call, never silence.
    _assert_no_raw_tool_call(delivered)


@pytest.mark.asyncio
async def test_pinned_owl_provider_does_not_escalate(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    tool_registry = ToolRegistry()
    tool_registry.register(_NoopTool())

    # The owl ("secretary") is pinned to the weak provider by NAME → no escalation.
    leaker = _provider("secretary", _leak_responder, "fast")
    strong = _provider("strong", _strong_responder, "powerful")
    preg = ProviderRegistry()
    preg.register_mock("secretary", leaker, tier="fast")
    preg.register_mock("strong", strong, tier="powerful")
    services = StepServices(
        provider_registry=preg,
        owl_registry=OwlRegistry.with_default_secretary(),
        tool_registry=tool_registry,
    )
    backend = AsyncioBackend(services=services)

    delivered = await _execute_turn(
        "create a guardrail skill for me", backend, session="sess-pin", trace="trace-pin",
    )

    # Pinned → honest floor from the weak provider, never the raw leak, never silence.
    _assert_no_raw_tool_call(delivered)
    # The stronger tier was NEVER consulted (no escalation off a pinned choice).
    assert not any(strong._client.tool_loop_calls), (  # type: ignore[attr-defined]
        "a PINNED owl choice escalated to the stronger tier — it must not"
    )
