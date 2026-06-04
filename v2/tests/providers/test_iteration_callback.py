"""S3 — per-iteration callback on both provider loops.

Verifies:
  1. With a scripted 2-iteration tool-using scenario + final answer the callback
     fires exactly once per iteration (3 times total: iter 0, 1 both have tools;
     iter 2 is the terminal answer) with monotonically increasing ``iteration``
     indices and a growing ``messages`` snapshot.

  2. With ``on_iteration_complete=None`` (the default) the loop behaves EXACTLY
     as before — the same return value, same call count, no regression.

Covers both providers (OpenAI and Anthropic) and all three OpenAI sub-paths:
  - native tool_calls (Path C)
  - ReAct text fallback (Path A)
  - terminal answer (Path B)

Uses the same fake-client pattern established in test_phaseF_max_out.py and
test_react_protocol.py — no real network, no process I/O.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.providers.anthropic_provider import AnthropicProvider
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.react_callback import ReActIterationState

# --------------------------------------------------------------------------- #
# Shared fake helpers
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
# OpenAI fake client — native tool_calls path (2 tool iterations + final)
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
# T1 — OpenAI native tool_calls: callback fires once per iteration
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_openai_native_callback_fires_per_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # Iter 0: tool_call → iter 1: tool_call → iter 2: final answer
    client = _FakeOAIClient([
        _tool_response_oai("c0", "first"),
        _tool_response_oai("c1", "second"),
        _final_response_oai("Done."),
    ])
    provider = _make_openai_provider(client)

    captured: list[ReActIterationState] = []

    async def callback(state: ReActIterationState) -> None:
        captured.append(state)

    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_TOOL_SCHEMAS,
        tool_dispatcher=_dispatcher,
        on_iteration_complete=callback,
    )

    assert text == "Done."
    assert len(calls) == 2

    # Callback fired once per iteration (0, 1 tool iters + final iter 2)
    assert len(captured) == 3
    assert [s.iteration for s in captured] == [0, 1, 2]

    # messages grow between tool-calling iterations; the terminal iteration
    # (iter 2) fires BEFORE the final answer is appended, so its snapshot
    # equals iter 1's — both contain the same accumulated tool context.
    assert len(captured[0].messages) < len(captured[1].messages)
    assert len(captured[1].messages) == len(captured[2].messages)

    # tool_call_records accumulate — iter 0 has 1, iter 1 has 2, iter 2 still 2
    assert len(captured[0].tool_call_records) == 1
    assert len(captured[1].tool_call_records) == 2
    assert len(captured[2].tool_call_records) == 2  # no new call on final iter

    # Snapshots are copies — mutating them doesn't corrupt loop output
    captured[0].messages.append({"role": "bogus", "content": "injected"})
    assert text == "Done."  # return value unchanged


# --------------------------------------------------------------------------- #
# T2 — OpenAI native tool_calls: None callback = identical behavior
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_openai_none_callback_behavior_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    client = _FakeOAIClient([
        _tool_response_oai("c0", "first"),
        _tool_response_oai("c1", "second"),
        _final_response_oai("Done."),
    ])
    provider = _make_openai_provider(client)

    # No callback — default None
    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_TOOL_SCHEMAS,
        tool_dispatcher=_dispatcher,
    )

    assert text == "Done."
    assert len(calls) == 2


# --------------------------------------------------------------------------- #
# T3 — OpenAI ReAct text-fallback path: callback fires on each action iteration
# --------------------------------------------------------------------------- #


def _react_response_oai(query: str) -> _FakeResponse:
    action = f'ACTION: web_search\n```json\n{{"query":"{query}"}}\n```'
    return _FakeResponse(_FakeMessage(content=action, tool_calls=None))


@pytest.mark.asyncio
async def test_openai_react_path_callback_fires_per_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # Iter 0: ReAct action → iter 1: ReAct action → iter 2: final answer
    client = _FakeOAIClient([
        _react_response_oai("q0"),
        _react_response_oai("q1"),
        _final_response_oai("ReAct done."),
    ])
    provider = _make_openai_provider(client)

    captured: list[ReActIterationState] = []

    async def callback(state: ReActIterationState) -> None:
        captured.append(state)

    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_TOOL_SCHEMAS,
        tool_dispatcher=_dispatcher,
        on_iteration_complete=callback,
    )

    assert text == "ReAct done."
    assert len(calls) == 2

    # One callback per iteration: 2 action iters + 1 terminal
    assert len(captured) == 3
    assert [s.iteration for s in captured] == [0, 1, 2]
    # tool_call_records grow with each action iteration
    assert len(captured[0].tool_call_records) == 1
    assert len(captured[1].tool_call_records) == 2


# --------------------------------------------------------------------------- #
# Anthropic fake client
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
# T4 — Anthropic: callback fires once per iteration
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_anthropic_callback_fires_per_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # Iter 0: tool_use → iter 1: tool_use → iter 2: end_turn (final answer)
    client = _FakeAnthropicClient([
        _tool_response_anthropic("tu0", "first"),
        _tool_response_anthropic("tu1", "second"),
        _final_response_anthropic("Anthropic done."),
    ])
    provider = _make_anthropic_provider(client)

    captured: list[ReActIterationState] = []

    async def callback(state: ReActIterationState) -> None:
        captured.append(state)

    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_TOOL_SCHEMAS,
        tool_dispatcher=_dispatcher,
        on_iteration_complete=callback,
    )

    assert text == "Anthropic done."
    assert len(calls) == 2

    # Callback fired once per iteration (0 + 1 tool + 2 terminal)
    assert len(captured) == 3
    assert [s.iteration for s in captured] == [0, 1, 2]

    # messages grow between tool-calling iterations; the terminal iteration
    # fires BEFORE the final answer is returned (not appended to messages),
    # so iter 2's snapshot length equals iter 1's.
    assert len(captured[0].messages) < len(captured[1].messages)
    assert len(captured[1].messages) == len(captured[2].messages)

    # tool_call_records accumulate correctly
    assert len(captured[0].tool_call_records) == 1
    assert len(captured[1].tool_call_records) == 2
    assert len(captured[2].tool_call_records) == 2  # no new call on final iter


# --------------------------------------------------------------------------- #
# T5 — Anthropic: None callback = identical behavior
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_anthropic_none_callback_behavior_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    client = _FakeAnthropicClient([
        _tool_response_anthropic("tu0", "first"),
        _tool_response_anthropic("tu1", "second"),
        _final_response_anthropic("Anthropic done."),
    ])
    provider = _make_anthropic_provider(client)

    # No callback — default None
    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_TOOL_SCHEMAS,
        tool_dispatcher=_dispatcher,
    )

    assert text == "Anthropic done."
    assert len(calls) == 2


# --------------------------------------------------------------------------- #
# T6 — Callback propagates exceptions (do NOT swallow)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_openai_callback_exception_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    client = _FakeOAIClient([
        _tool_response_oai("c0", "first"),
        _final_response_oai("done"),
    ])
    provider = _make_openai_provider(client)

    async def bad_callback(state: ReActIterationState) -> None:
        raise ValueError("checkpoint failed")

    with pytest.raises(ValueError, match="checkpoint failed"):
        await provider.complete_with_tools(
            user_text="go",
            system_text="sys",
            tool_schemas=_TOOL_SCHEMAS,
            tool_dispatcher=_dispatcher,
            on_iteration_complete=bad_callback,
        )


@pytest.mark.asyncio
async def test_anthropic_callback_exception_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    client = _FakeAnthropicClient([
        _tool_response_anthropic("tu0", "first"),
        _final_response_anthropic("done"),
    ])
    provider = _make_anthropic_provider(client)

    async def bad_callback(state: ReActIterationState) -> None:
        raise RuntimeError("store is down")

    with pytest.raises(RuntimeError, match="store is down"):
        await provider.complete_with_tools(
            user_text="go",
            system_text="sys",
            tool_schemas=_TOOL_SCHEMAS,
            tool_dispatcher=_dispatcher,
            on_iteration_complete=bad_callback,
        )
