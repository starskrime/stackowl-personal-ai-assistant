"""Task 9 (concurrent-msg §5.1) — callback splice contract.

The ``IterationCallback`` now returns ``list[dict[str, Any]] | None``: a list of
messages the provider must FOLD into its live ``messages`` list (so the NEXT LLM
round sees them), or ``None`` to fold nothing.  This is the substrate for
live-steer (P2): a callback injects a ``[steering]`` user message between ReAct
iterations and the running loop observes it on its next call.

Verifies for BOTH providers (OpenAI native tool_calls + Anthropic tool_use):
  1. A callback returning ``[{"role": "user", "content": "[steering] ..."}]`` on
     iteration 0 has that message present in the SECOND LLM call's ``messages``
     (the fold is observed by the live loop, not lost to a defensive copy).
  2. A callback returning ``None`` folds nothing — the second call's messages do
     NOT contain the steering text.

Reuses the scripted fake-client pattern from test_iteration_callback.py — no
real network, no process I/O.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.providers.anthropic_provider import AnthropicProvider
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.react_callback import ReActIterationState

_STEER_TEXT = "[steering] also include Y"

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


def _contains_steering(messages: list[dict[str, Any]]) -> bool:
    """True if any message in the list carries the steering text (any content shape)."""
    for m in messages:
        content = m.get("content")
        if isinstance(content, str) and _STEER_TEXT in content:
            return True
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and _STEER_TEXT in str(block.get("text", "")):
                    return True
                if isinstance(block, str) and _STEER_TEXT in block:
                    return True
    return False


# --------------------------------------------------------------------------- #
# OpenAI fakes — a recording scripted client that snapshots messages per call
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


class _RecordingCompletions:
    """Replays a fixed list of responses, snapshotting the messages of each call."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self._idx = 0
        self.seen_messages: list[list[dict[str, Any]]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        # Deep-ish snapshot: copy the list so later mutation doesn't rewrite history.
        self.seen_messages.append([dict(m) for m in kwargs["messages"]])
        resp = self._responses[self._idx]
        self._idx += 1
        return resp


class _FakeChat:
    def __init__(self, completions: _RecordingCompletions) -> None:
        self.completions = completions


class _FakeOAIClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.chat = _FakeChat(_RecordingCompletions(responses))


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


@pytest.mark.asyncio
async def test_openai_callback_returned_messages_are_spliced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # Iter 0: tool_call → iter 1: final answer. Fold steering after iter 0.
    client = _FakeOAIClient([
        _tool_response_oai("c0", "first"),
        _final_response_oai("Done."),
    ])
    provider = _make_openai_provider(client)

    folded = [{"role": "user", "content": _STEER_TEXT}]

    async def cb(state: ReActIterationState) -> list[dict[str, Any]] | None:
        return folded if state.iteration == 0 else None

    text, _ = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_TOOL_SCHEMAS,
        tool_dispatcher=_dispatcher,
        on_iteration_complete=cb,
    )

    assert text == "Done."
    seen = client.chat.completions.seen_messages
    assert len(seen) == 2
    # First LLM call (iteration 0) did NOT see the steering message yet.
    assert not _contains_steering(seen[0])
    # Second LLM call (iteration 1) DID — the fold landed on the live list.
    assert _contains_steering(seen[1])


@pytest.mark.asyncio
async def test_openai_callback_none_folds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    client = _FakeOAIClient([
        _tool_response_oai("c0", "first"),
        _final_response_oai("Done."),
    ])
    provider = _make_openai_provider(client)

    async def cb(state: ReActIterationState) -> list[dict[str, Any]] | None:
        return None

    text, _ = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_TOOL_SCHEMAS,
        tool_dispatcher=_dispatcher,
        on_iteration_complete=cb,
    )

    assert text == "Done."
    seen = client.chat.completions.seen_messages
    assert len(seen) == 2
    assert not _contains_steering(seen[1])


# --------------------------------------------------------------------------- #
# Anthropic fakes
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


class _RecordingAnthropicMessages:
    def __init__(self, responses: list[_AResponse]) -> None:
        self._responses = responses
        self._idx = 0
        self.seen_messages: list[list[dict[str, Any]]] = []

    async def create(self, **kwargs: Any) -> _AResponse:
        self.seen_messages.append([dict(m) for m in kwargs["messages"]])
        resp = self._responses[self._idx]
        self._idx += 1
        return resp


class _FakeAnthropicClient:
    def __init__(self, responses: list[_AResponse]) -> None:
        self.messages = _RecordingAnthropicMessages(responses)


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


@pytest.mark.asyncio
async def test_anthropic_callback_returned_messages_are_spliced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    client = _FakeAnthropicClient([
        _tool_response_anthropic("tu0", "first"),
        _final_response_anthropic("Anthropic done."),
    ])
    provider = _make_anthropic_provider(client)

    folded = [{"role": "user", "content": _STEER_TEXT}]

    async def cb(state: ReActIterationState) -> list[dict[str, Any]] | None:
        return folded if state.iteration == 0 else None

    text, _ = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_TOOL_SCHEMAS,
        tool_dispatcher=_dispatcher,
        on_iteration_complete=cb,
    )

    assert text == "Anthropic done."
    seen = client.messages.seen_messages
    assert len(seen) == 2
    assert not _contains_steering(seen[0])
    assert _contains_steering(seen[1])


@pytest.mark.asyncio
async def test_anthropic_callback_none_folds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    client = _FakeAnthropicClient([
        _tool_response_anthropic("tu0", "first"),
        _final_response_anthropic("Anthropic done."),
    ])
    provider = _make_anthropic_provider(client)

    async def cb(state: ReActIterationState) -> list[dict[str, Any]] | None:
        return None

    text, _ = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_TOOL_SCHEMAS,
        tool_dispatcher=_dispatcher,
        on_iteration_complete=cb,
    )

    assert text == "Anthropic done."
    seen = client.messages.seen_messages
    assert len(seen) == 2
    assert not _contains_steering(seen[1])
