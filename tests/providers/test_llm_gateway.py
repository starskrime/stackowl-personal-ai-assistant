"""LLMGateway — escalation ladder, sentinel detection, tool-capability climb."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from stackowl.exceptions import CircuitOpenError, ProviderError
from stackowl.providers.base import CompletionResult, Message
from stackowl.providers.llm_gateway import (
    ESCALATE_INSTRUCTION,
    LLMGateway,
    is_escalate_signal,
    tier_span,
)


class _FakeProvider:
    def __init__(self, name: str, *, reply: str, supports_tools: bool = True) -> None:
        self.name = name
        self._reply = reply
        self.supports_tools = supports_tools
        self.complete_calls: list[list[Message]] = []
        self.tool_calls: list[str | None] = []
        # What the gateway handed complete_with_tools (escalation-wiring assertions).
        self.tool_schemas_seen: list[Any] = []
        self.can_escalate_seen: list[bool] = []
        self.wrapup_seen: list[Any] = []

    async def complete(self, messages: list[Message], model: str, **kwargs: Any) -> CompletionResult:
        self.complete_calls.append(messages)
        return CompletionResult(
            content=self._reply, input_tokens=1, output_tokens=1, model=self.name,
            provider_name=self.name, duration_ms=1.0,
        )

    async def complete_with_tools(self, *, user_text: str, system_text: str | None,
                                  tool_schemas: list, tool_dispatcher: Any, **kwargs: Any):
        self.tool_calls.append(system_text)
        self.tool_schemas_seen.append(tool_schemas)
        self.can_escalate_seen.append(kwargs.get("can_escalate"))
        self.wrapup_seen.append(kwargs.get("wrapup_deadline_s"))
        return self._reply, [{"name": "t", "failed": False}]


class _FaultyProvider(_FakeProvider):
    """A provider whose every call raises a classified provider fault (F-16/F-17)."""

    def __init__(self, name: str, *, fault: BaseException, supports_tools: bool = True) -> None:
        super().__init__(name, reply="(never returned)", supports_tools=supports_tools)
        self._fault = fault

    async def complete(self, messages: list[Message], model: str, **kwargs: Any) -> CompletionResult:
        self.complete_calls.append(messages)
        raise self._fault

    async def complete_with_tools(self, *, user_text: str, system_text: str | None,
                                  tool_schemas: list, tool_dispatcher: Any, **kwargs: Any):
        self.tool_calls.append(system_text)
        raise self._fault


class _FakeRegistry:
    def __init__(self, by_tier: dict[str, _FakeProvider]) -> None:
        self._by_tier = by_tier

    def resolve_tier_with_fallback(self, tier: str):
        return self._by_tier[tier], None


def _sys(content: str) -> Message:
    return Message(role="system", content=content)


def _user(content: str) -> Message:
    return Message(role="user", content=content)


# -- helpers ----------------------------------------------------------------- #


def test_tier_span_slices() -> None:
    assert tier_span("fast", "powerful") == ["fast", "standard", "powerful"]
    assert tier_span("fast", "fast") == ["fast"]
    assert tier_span("standard", "powerful") == ["standard", "powerful"]
    # ceiling below floor degenerates to the floor only
    assert tier_span("powerful", "fast") == ["powerful"]
    # unknown tiers fall back to full ladder bounds (never empty)
    assert tier_span("???", "???") == ["fast", "standard", "powerful"]


def test_is_escalate_signal_variants() -> None:
    assert is_escalate_signal("ESCALATE")
    assert is_escalate_signal("  escalate. ")
    assert is_escalate_signal("'ESCALATE'")
    assert not is_escalate_signal("ESCALATE now please")
    assert not is_escalate_signal("the answer is 42")
    assert not is_escalate_signal(None)
    assert not is_escalate_signal("")


# -- complete ---------------------------------------------------------------- #


def test_complete_no_escalation_returns_fast() -> None:
    fast = _FakeProvider("fast", reply="here is your answer")
    gw = LLMGateway(_FakeRegistry({"fast": fast}))
    out = asyncio.run(gw.complete([_user("hi")], floor="fast", ceiling="powerful"))
    assert out.content == "here is your answer"
    assert out.model == "fast"


def test_complete_escalates_on_sentinel() -> None:
    fast = _FakeProvider("fast", reply="ESCALATE")
    standard = _FakeProvider("standard", reply="a thoughtful answer")
    powerful = _FakeProvider("powerful", reply="should not reach")
    gw = LLMGateway(_FakeRegistry({"fast": fast, "standard": standard, "powerful": powerful}))
    out = asyncio.run(gw.complete([_sys("base"), _user("hard q")], floor="fast", ceiling="powerful"))
    assert out.model == "standard"
    assert out.content == "a thoughtful answer"
    assert len(fast.complete_calls) == 1
    assert len(powerful.complete_calls) == 0  # stopped once standard answered


def test_complete_escalation_instruction_injected_below_ceiling_only() -> None:
    fast = _FakeProvider("fast", reply="ESCALATE")
    powerful = _FakeProvider("powerful", reply="final")
    gw = LLMGateway(_FakeRegistry({"fast": fast, "standard": fast, "powerful": powerful}))
    asyncio.run(gw.complete([_sys("base")], floor="fast", ceiling="powerful"))
    # fast got the escalate instruction; powerful (ceiling) did not.
    assert ESCALATE_INSTRUCTION in fast.complete_calls[0][0].content
    assert ESCALATE_INSTRUCTION not in powerful.complete_calls[0][0].content


def test_complete_pinned_tier_does_not_escalate() -> None:
    # floor == ceiling: a sentinel reply is returned as-is (meta-call, no recursion).
    fast = _FakeProvider("fast", reply="ESCALATE")
    gw = LLMGateway(_FakeRegistry({"fast": fast}))
    out = asyncio.run(gw.complete([_user("x")], floor="fast", ceiling="fast"))
    assert out.content == "ESCALATE"
    assert ESCALATE_INSTRUCTION not in fast.complete_calls[0][0].content


# -- complete_with_tools ----------------------------------------------------- #


def test_tools_escalate_mid_loop_calls_on_escalate() -> None:
    fast = _FakeProvider("fast", reply="ESCALATE")
    powerful = _FakeProvider("powerful", reply="done with tools")
    gw = LLMGateway(_FakeRegistry({"fast": fast, "standard": fast, "powerful": powerful}))
    resets: list[tuple[str, str]] = []

    async def on_escalate(frm: str, to: str) -> None:
        resets.append((frm, to))

    text, calls = asyncio.run(gw.complete_with_tools(
        user_text="do it", system_text="sys", tool_schemas=[{"name": "t"}],
        tool_dispatcher=None, floor="fast", ceiling="powerful", on_escalate=on_escalate,
    ))
    assert text == "done with tools"
    assert resets  # ledger-reset hook fired on escalation


def test_tools_skip_non_tool_capable_tier() -> None:
    fast = _FakeProvider("fast", reply="x", supports_tools=False)
    standard = _FakeProvider("standard", reply="ran the loop", supports_tools=True)
    gw = LLMGateway(_FakeRegistry({"fast": fast, "standard": standard, "powerful": standard}))
    text, _ = asyncio.run(gw.complete_with_tools(
        user_text="u", system_text="s", tool_schemas=[{"name": "t"}],
        tool_dispatcher=None, floor="fast", ceiling="powerful",
    ))
    assert text == "ran the loop"
    assert fast.tool_calls == []  # never invoked — not tool-capable


def test_tools_no_escalation_when_fast_answers() -> None:
    fast = _FakeProvider("fast", reply="quick answer")
    powerful = _FakeProvider("powerful", reply="unused")
    gw = LLMGateway(_FakeRegistry({"fast": fast, "standard": fast, "powerful": powerful}))
    text, _ = asyncio.run(gw.complete_with_tools(
        user_text="u", system_text="s", tool_schemas=[{"name": "t"}],
        tool_dispatcher=None, floor="fast", ceiling="powerful",
    ))
    assert text == "quick answer"
    assert powerful.tool_calls == []


# -- escalation-wiring: per-tier schema rebuild + can_escalate flag ----------- #


def test_tools_rebuilds_schemas_per_tier_and_passes_can_escalate() -> None:
    fast = _FakeProvider("fast", reply="ESCALATE")
    standard = _FakeProvider("standard", reply="done")
    gw = LLMGateway(_FakeRegistry({"fast": fast, "standard": standard}))

    def build(provider: _FakeProvider) -> list:
        return [{"name": f"schema_{provider.name}"}]

    text, _ = asyncio.run(gw.complete_with_tools(
        user_text="u", system_text="s", tool_schemas=[{"name": "ORIGINAL"}],
        tool_dispatcher=None, floor="fast", ceiling="standard", build_tool_schemas=build,
    ))
    assert text == "done"
    # Each tier's provider received schemas REBUILT for itself, not the passed-in list.
    assert fast.tool_schemas_seen == [[{"name": "schema_fast"}]]
    assert standard.tool_schemas_seen == [[{"name": "schema_standard"}]]
    # can_escalate is True below the ceiling, False at the ceiling.
    assert fast.can_escalate_seen == [True]
    assert standard.can_escalate_seen == [False]


def test_tools_build_schemas_may_be_async() -> None:
    fast = _FakeProvider("fast", reply="answer")
    gw = LLMGateway(_FakeRegistry({"fast": fast}))

    async def build(provider: _FakeProvider) -> list:
        return [{"name": "async_schema"}]

    asyncio.run(gw.complete_with_tools(
        user_text="u", system_text="s", tool_schemas=[], tool_dispatcher=None,
        floor="fast", ceiling="fast", build_tool_schemas=build,
    ))
    assert fast.tool_schemas_seen == [[{"name": "async_schema"}]]


def test_tools_wrapup_deadline_recomputed_per_attempt() -> None:
    fast = _FakeProvider("fast", reply="ESCALATE")
    standard = _FakeProvider("standard", reply="done")
    gw = LLMGateway(_FakeRegistry({"fast": fast, "standard": standard}))
    residual = iter([30.0, 20.0])

    text, _ = asyncio.run(gw.complete_with_tools(
        user_text="u", system_text="s", tool_schemas=[], tool_dispatcher=None,
        floor="fast", ceiling="standard", wrapup_deadline_fn=lambda: next(residual),
    ))
    assert text == "done"
    # A fresh residual budget is computed for each tier attempt.
    assert fast.wrapup_seen == [30.0]
    assert standard.wrapup_seen == [20.0]


def test_tools_back_compat_uses_passed_schemas_when_no_builder() -> None:
    fast = _FakeProvider("fast", reply="answer")
    gw = LLMGateway(_FakeRegistry({"fast": fast}))
    asyncio.run(gw.complete_with_tools(
        user_text="u", system_text="s", tool_schemas=[{"name": "passed"}],
        tool_dispatcher=None, floor="fast", ceiling="fast",
    ))
    assert fast.tool_schemas_seen == [[{"name": "passed"}]]
    assert fast.can_escalate_seen == [False]


# -- F-16/F-17: provider-FAULT fallback (not just ESCALATE success) ----------- #


def test_complete_falls_back_to_higher_tier_on_provider_fault() -> None:
    # Floor tier raises a classified provider fault (e.g. circuit OPEN on first trip);
    # a higher tier answers normally → the gateway returns the higher-tier result.
    fast = _FaultyProvider("fast", fault=CircuitOpenError("fast", 30.0))
    standard = _FakeProvider("standard", reply="recovered answer")
    powerful = _FakeProvider("powerful", reply="unused")
    gw = LLMGateway(_FakeRegistry({"fast": fast, "standard": standard, "powerful": powerful}))
    out = asyncio.run(gw.complete([_user("hi")], floor="fast", ceiling="powerful"))
    assert out.content == "recovered answer"
    assert out.model == "standard"
    assert len(fast.complete_calls) == 1  # floor was attempted then cascaded
    assert len(powerful.complete_calls) == 0  # stopped once standard answered


def test_complete_reraises_provider_fault_at_last_tier() -> None:
    # No headroom (single tier / at the ceiling): a fault must propagate, not be swallowed.
    fault = ProviderError("powerful", ConnectionError("down"))
    powerful = _FaultyProvider("powerful", fault=fault)
    gw = LLMGateway(_FakeRegistry({"powerful": powerful}))
    with pytest.raises(ProviderError):
        asyncio.run(gw.complete([_user("hi")], floor="powerful", ceiling="powerful"))


def test_complete_non_fault_error_is_not_caught() -> None:
    # A non-provider-fault error (our own bug) must propagate immediately, no cascade.
    boom = _FaultyProvider("fast", fault=RuntimeError("our bug"))
    standard = _FakeProvider("standard", reply="should not reach")
    gw = LLMGateway(_FakeRegistry({"fast": boom, "standard": standard}))
    with pytest.raises(RuntimeError):
        asyncio.run(gw.complete([_user("hi")], floor="fast", ceiling="standard"))
    assert len(standard.complete_calls) == 0  # no fallback for a non-fault error


def test_tools_falls_back_to_higher_tier_on_provider_fault() -> None:
    fast = _FaultyProvider("fast", fault=CircuitOpenError("fast", 30.0))
    standard = _FakeProvider("standard", reply="done with tools")
    gw = LLMGateway(_FakeRegistry({"fast": fast, "standard": standard, "powerful": standard}))
    resets: list[tuple[str, str]] = []

    async def on_escalate(frm: str, to: str) -> None:
        resets.append((frm, to))

    text, _ = asyncio.run(gw.complete_with_tools(
        user_text="u", system_text="s", tool_schemas=[{"name": "t"}],
        tool_dispatcher=None, floor="fast", ceiling="powerful", on_escalate=on_escalate,
    ))
    assert text == "done with tools"
    assert resets == [("fast", "standard")]  # ledger reset before the recovery tier


def test_tools_reraises_provider_fault_at_last_tier() -> None:
    powerful = _FaultyProvider("powerful", fault=ProviderError("powerful", TimeoutError("hung")))
    gw = LLMGateway(_FakeRegistry({"powerful": powerful}))
    with pytest.raises(ProviderError):
        asyncio.run(gw.complete_with_tools(
            user_text="u", system_text="s", tool_schemas=[{"name": "t"}],
            tool_dispatcher=None, floor="powerful", ceiling="powerful",
        ))
