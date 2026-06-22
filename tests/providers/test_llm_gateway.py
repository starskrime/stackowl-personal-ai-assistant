"""LLMGateway — escalation ladder, sentinel detection, tool-capability climb."""

from __future__ import annotations

import asyncio
from typing import Any

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

    async def complete(self, messages: list[Message], model: str, **kwargs: Any) -> CompletionResult:
        self.complete_calls.append(messages)
        return CompletionResult(
            content=self._reply, input_tokens=1, output_tokens=1, model=self.name,
            provider_name=self.name, duration_ms=1.0,
        )

    async def complete_with_tools(self, *, user_text: str, system_text: str | None,
                                  tool_schemas: list, tool_dispatcher: Any, **kwargs: Any):
        self.tool_calls.append(system_text)
        return self._reply, [{"name": "t", "failed": False}]


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
