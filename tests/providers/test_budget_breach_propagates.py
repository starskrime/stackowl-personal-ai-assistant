"""E2-S4 — a BudgetBreach from on_iteration_complete breaks the provider tool loop.

Regression guard: providers must await the callback (not fire-and-forget); a raise
inside the callback must propagate OUT of complete_with_tools.  A future refactor
that wraps the call in asyncio.create_task() would silently swallow the breach —
these tests catch that.

Harness reused verbatim from test_iteration_callback.py (the established sibling).
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import BudgetBreach
from stackowl.providers.anthropic_provider import AnthropicProvider
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.react_callback import ReActIterationState

# --------------------------------------------------------------------------- #
# Shared callback that always raises BudgetBreach
# --------------------------------------------------------------------------- #


async def _raise_cb(state: ReActIterationState) -> None:
    raise BudgetBreach("steps", 1, 1)


# --------------------------------------------------------------------------- #
# Shared minimal tool schema (same used by sibling tests)
# --------------------------------------------------------------------------- #

_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    }
]


async def _dispatcher(name: str, args: dict[str, Any]) -> str:
    return f"result_for_{name}"


# --------------------------------------------------------------------------- #
# OpenAI fake client — reused verbatim from test_iteration_callback.py
# --------------------------------------------------------------------------- #


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, tc_id: str, name: str, arguments: str) -> None:
        self.id = tc_id
        self.type = "function"
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content: str | None, tool_calls: list[_FakeToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]
        self.model = "test-model"


class _ScriptedCompletions:
    """Replays a fixed list of responses in order."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self._idx = 0

    async def create(self, **kwargs: Any) -> _FakeResponse:
        resp = self._responses[self._idx]
        self._idx += 1
        return resp


class _FakeChat:
    def __init__(self, completions: _ScriptedCompletions) -> None:
        self.completions = completions


class _FakeOAIClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.chat = _FakeChat(_ScriptedCompletions(responses))


def _make_openai_provider(client: _FakeOAIClient) -> OpenAIProvider:
    config = ProviderConfig(
        name="test",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="test-model",
        tier="local",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = client  # type: ignore[assignment]
    return provider


def _tool_response_oai(tc_id: str, query: str) -> _FakeResponse:
    tc = _FakeToolCall(tc_id, "web_search", f'{{"query":"{query}"}}')
    return _FakeResponse(_FakeMessage(content=None, tool_calls=[tc]))


def _final_response_oai(text: str) -> _FakeResponse:
    return _FakeResponse(_FakeMessage(content=text, tool_calls=None))


# --------------------------------------------------------------------------- #
# Anthropic fake client — reused verbatim from test_iteration_callback.py
# --------------------------------------------------------------------------- #


class _ABlock:
    def __init__(
        self,
        block_type: str,
        text: str = "",
        block_id: str = "",
        name: str = "",
        input: Any = None,
    ) -> None:
        self.type = block_type
        self.text = text
        self.id = block_id
        self.name = name
        self.input = input or {}


class _AResponse:
    def __init__(self, stop_reason: str, content: list[_ABlock]) -> None:
        self.stop_reason = stop_reason
        self.content = content


class _ScriptedAnthropicMessages:
    """Replays a fixed list of Anthropic responses in order."""

    def __init__(self, responses: list[_AResponse]) -> None:
        self._responses = responses
        self._idx = 0

    async def create(self, **kwargs: Any) -> _AResponse:
        resp = self._responses[self._idx]
        self._idx += 1
        return resp


class _FakeAnthropicClient:
    def __init__(self, responses: list[_AResponse]) -> None:
        self.messages = _ScriptedAnthropicMessages(responses)


def _make_anthropic_provider(client: _FakeAnthropicClient) -> AnthropicProvider:
    config = ProviderConfig(
        name="claude",
        protocol="anthropic",
        default_model="claude-sonnet",
        tier="powerful",
    )
    provider = AnthropicProvider(config, api_key="x")
    provider._client = client  # type: ignore[assignment]
    return provider


def _tool_response_anthropic(tc_id: str, query: str) -> _AResponse:
    return _AResponse(
        "tool_use",
        [_ABlock("tool_use", block_id=tc_id, name="web_search", input={"query": query})],
    )


def _final_response_anthropic(text: str) -> _AResponse:
    return _AResponse("end_turn", [_ABlock("text", text=text)])


# --------------------------------------------------------------------------- #
# T1 — AnthropicProvider: BudgetBreach propagates out of complete_with_tools
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_anthropic_propagates_budget_breach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BudgetBreach raised inside on_iteration_complete must propagate out of
    complete_with_tools for AnthropicProvider (not swallowed / fire-and-forgot).

    The fake backend returns a tool-use response first, which forces one full
    iteration (tool call → dispatcher → callback → raise).
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    client = _FakeAnthropicClient([
        _tool_response_anthropic("tu0", "first"),
        _final_response_anthropic("should never reach here"),
    ])
    provider = _make_anthropic_provider(client)

    with pytest.raises(BudgetBreach):
        await provider.complete_with_tools(
            user_text="do a tool call",
            system_text="",
            tool_schemas=_TOOL_SCHEMAS,
            tool_dispatcher=_dispatcher,
            on_iteration_complete=_raise_cb,
        )


# --------------------------------------------------------------------------- #
# T2 — OpenAIProvider: BudgetBreach propagates out of complete_with_tools
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_openai_propagates_budget_breach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BudgetBreach raised inside on_iteration_complete must propagate out of
    complete_with_tools for OpenAIProvider (not swallowed / fire-and-forgot).

    The fake backend returns a native tool_calls response first, which forces one
    full iteration (tool call → dispatcher → callback → raise).
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    client = _FakeOAIClient([
        _tool_response_oai("c0", "first"),
        _final_response_oai("should never reach here"),
    ])
    provider = _make_openai_provider(client)

    with pytest.raises(BudgetBreach):
        await provider.complete_with_tools(
            user_text="do a tool call",
            system_text="",
            tool_schemas=_TOOL_SCHEMAS,
            tool_dispatcher=_dispatcher,
            on_iteration_complete=_raise_cb,
        )
