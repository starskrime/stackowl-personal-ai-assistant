"""Auto-escalation on objective failure (not model self-report).

A weak tier that spirals through its whole tool budget — or whose answer the
persistence judge rules a give-up — must hand the turn UP to a stronger tier
instead of wrapping up a weak answer. The weak model never emits ``ESCALATE``
itself, so ``complete_with_tools`` returns the sentinel on the objective signal
when ``can_escalate`` is set; at the top tier (``can_escalate`` False) the
graceful wrap-up floor still applies.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.providers.llm_gateway import ESCALATE_SENTINEL
from stackowl.providers.openai_provider import OpenAIProvider


class _Fn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
        self.type = "function"
        self.function = _Fn(name, arguments)


class _Msg:
    def __init__(self, content: str | None, tool_calls: list[_ToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, msg: _Msg) -> None:
        self.message = msg


class _Resp:
    def __init__(self, msg: _Msg) -> None:
        self.choices = [_Choice(msg)]
        self.model = "qwen3.5:2b"
        self.usage = None


class _NeverStopsCompletions:
    """Emits a (distinct) tool call on EVERY round — never a final answer, so the
    loop runs to max_iterations (the spiral the weak 2b hit)."""

    def __init__(self) -> None:
        self.n = 0

    async def create(self, **kwargs: Any) -> _Resp:
        self.n += 1
        tc = _ToolCall(f"c{self.n}", "web_search", f'{{"query":"q{self.n}"}}')
        return _Resp(_Msg(content=None, tool_calls=[tc]))


class _Chat:
    def __init__(self, c: _NeverStopsCompletions) -> None:
        self.completions = c


class _Client:
    def __init__(self, c: _NeverStopsCompletions) -> None:
        self.chat = _Chat(c)


def _provider(c: _Client) -> OpenAIProvider:
    cfg = ProviderConfig(
        name="ollama", protocol="openai", base_url="http://localhost:11434/v1",
        default_model="qwen3.5:2b", tier="fast",
    )
    p = OpenAIProvider(cfg, api_key="")
    p._client = c  # type: ignore[assignment]
    return p


_SCHEMAS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "web_search", "description": "Search.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}}
]


async def _ok_dispatch(name: str, args: dict[str, Any]) -> str:
    return "some result"


@pytest.mark.asyncio
async def test_budget_exhaustion_escalates_when_possible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    provider = _provider(_Client(_NeverStopsCompletions()))

    text, calls = await provider.complete_with_tools(
        user_text="do a hard multi-step task", system_text="sys",
        tool_schemas=_SCHEMAS, tool_dispatcher=_ok_dispatch,
        can_escalate=True, max_iterations=3,
    )

    assert text == ESCALATE_SENTINEL, "a spiral that maxes out must escalate, not floor weak"


@pytest.mark.asyncio
async def test_budget_exhaustion_floors_at_top_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    provider = _provider(_Client(_NeverStopsCompletions()))

    text, calls = await provider.complete_with_tools(
        user_text="do a hard multi-step task", system_text="sys",
        tool_schemas=_SCHEMAS, tool_dispatcher=_ok_dispatch,
        can_escalate=False, max_iterations=3,
    )

    # Top tier: no escalation — the graceful wrap-up floor produces a real answer.
    assert text != ESCALATE_SENTINEL
    assert text.strip()


# ---------------------------------------------------------------------------
# PA3: a same-tool circuit breaker that opens mid-turn feeds the EXISTING tier
# ladder instead of dead-ending. The dispatcher below stands in for the pipeline
# breaker: after N failures it calls request_escalation() (exactly what
# _dispatch's circuit-open branch does). The provider loop must then escalate
# when can_escalate is set, and floor (NOT escalate) at the ceiling.
# ---------------------------------------------------------------------------


class _BreakerDispatch:
    """Simulates the pipeline breaker: trips request_escalation after `trip_at` calls."""

    def __init__(self, trip_at: int) -> None:
        self.n = 0
        self._trip_at = trip_at

    async def __call__(self, name: str, args: dict[str, Any]) -> str:
        from stackowl.providers.escalation_signal import request_escalation

        self.n += 1
        if self.n >= self._trip_at:
            request_escalation(name)
        return "boom — tool failed"


@pytest.mark.asyncio
async def test_circuit_open_escalates_when_possible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stackowl.providers.escalation_signal import clear_escalation

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    clear_escalation()
    provider = _provider(_Client(_NeverStopsCompletions()))
    try:
        text, _calls = await provider.complete_with_tools(
            user_text="do a hard task", system_text="sys",
            tool_schemas=_SCHEMAS, tool_dispatcher=_BreakerDispatch(trip_at=2),
            can_escalate=True, max_iterations=8,
        )
        assert text == ESCALATE_SENTINEL, "a circuit opened mid-turn must escalate, not dead-end"
    finally:
        clear_escalation()


@pytest.mark.asyncio
async def test_circuit_open_does_not_escalate_at_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stackowl.providers.escalation_signal import clear_escalation

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    clear_escalation()
    provider = _provider(_Client(_NeverStopsCompletions()))
    try:
        text, _calls = await provider.complete_with_tools(
            user_text="do a hard task", system_text="sys",
            tool_schemas=_SCHEMAS, tool_dispatcher=_BreakerDispatch(trip_at=2),
            can_escalate=False, max_iterations=3,
        )
        # At the ceiling the flag is ignored → existing wrap-up floor, never the sentinel.
        assert text != ESCALATE_SENTINEL
        assert text.strip()
    finally:
        clear_escalation()
