"""W2.T9 — provider empty wrap-up returns the honest never-empty floor, not "".

Phase F makes ONE final tool-free wrap-up call at max-out. If THAT call also
yields empty content AND there is no prior assistant text to fall back to, the
providers previously returned ``"", all_calls`` — handing the user silence.

T9 wires :func:`stackowl.pipeline.supervisor.synthesize_from_calls` into BOTH
providers' empty/fallback path so the user always gets an honest floor that names
the failed capability. These two tests drive each provider so the wrap-up path
yields EMPTY text and assert a NON-EMPTY floored string is returned.

Mirrors the fake-client harness from ``test_phaseF_max_out.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.pipeline.persistence import TOOL_FAILED_MARKER
from stackowl.providers.anthropic_provider import AnthropicProvider
from stackowl.providers.openai_provider import OpenAIProvider

# --------------------------------------------------------------------------- #
# OpenAI fake client — tool call every round, then an EMPTY wrap-up.
# --------------------------------------------------------------------------- #


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
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
        self.model = "gemma4:e4b"


class _EmptyWrapupCompletions:
    """Tool call on every tool-bearing round; EMPTY content on the wrap-up call.

    The dispatched tool result carries the failure marker so the recorded call is
    ``failed=True`` — the floor must then name the failed capability.
    """

    def __init__(self) -> None:
        self.tools_seen: list[bool] = []
        self.create_count = 0

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.create_count += 1
        has_tools = "tools" in kwargs and kwargs["tools"] is not None
        self.tools_seen.append(has_tools)
        if not has_tools:
            # The wrap-up call — return EMPTY content (no answer).
            return _FakeResponse(_FakeMessage(content="", tool_calls=None))
        tc = _FakeToolCall(
            id=f"call_{self.create_count}",
            name="web_search",
            arguments=f'{{"query":"q{self.create_count}"}}',
        )
        # content=None so no prior assistant text exists to fall back to.
        return _FakeResponse(_FakeMessage(content=None, tool_calls=[tc]))


class _FakeChat:
    def __init__(self, completions: _EmptyWrapupCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: _EmptyWrapupCompletions) -> None:
        self.chat = _FakeChat(completions)


def _make_openai_provider(client: _FakeClient) -> OpenAIProvider:
    config = ProviderConfig(
        name="ollama",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="gemma4:e4b",
        tier="local",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = client  # type: ignore[assignment]
    return provider


_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    }
]

async def _failing_dispatcher(name: str, args: dict[str, Any]) -> str:
    # Marker => the recorded call is classified failed=True.
    return f"{TOOL_FAILED_MARKER}could not reach the web"


@pytest.mark.asyncio
async def test_openai_empty_wrapup_returns_floor_not_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _EmptyWrapupCompletions()
    provider = _make_openai_provider(_FakeClient(completions))

    text, calls = await provider.complete_with_tools(
        user_text="search the web for me",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_failing_dispatcher,
    )

    # Was "" before T9 — now the honest non-empty floor.
    assert text, "empty wrap-up must yield a non-empty floored answer, not blank"
    assert text.strip() != ""
    # The floor names the failed capability (web_search).
    assert "web_search" in text.lower()
    # The wrap-up call was made (tool-free) yet still produced empty.
    assert completions.tools_seen[-1] is False


# --------------------------------------------------------------------------- #
# Anthropic mirror.
# --------------------------------------------------------------------------- #


class _ABlock:
    def __init__(self, type: str, text: str = "", id: str = "", name: str = "", input: Any = None) -> None:
        self.type = type
        self.text = text
        self.id = id
        self.name = name
        self.input = input or {}


class _AResponse:
    def __init__(self, stop_reason: str, content: list[_ABlock]) -> None:
        self.stop_reason = stop_reason
        self.content = content


class _AnthropicEmptyWrapupMessages:
    """tool_use on every tool-bearing call; EMPTY text on the wrap-up call."""

    def __init__(self) -> None:
        self.tools_seen: list[bool] = []
        self.create_count = 0

    async def create(self, **kwargs: Any) -> _AResponse:
        self.create_count += 1
        has_tools = "tools" in kwargs and kwargs["tools"] is not None
        self.tools_seen.append(has_tools)
        if not has_tools:
            # Wrap-up call — empty text block (no answer), no assistant fallback.
            return _AResponse("end_turn", [_ABlock("text", text="")])
        return _AResponse(
            "tool_use",
            [
                _ABlock(
                    "tool_use",
                    id=f"tu_{self.create_count}",
                    name="web_search",
                    input={"query": f"q{self.create_count}"},
                )
            ],
        )


class _AnthropicClient:
    def __init__(self, messages: _AnthropicEmptyWrapupMessages) -> None:
        self.messages = messages


def _make_anthropic_provider(client: _AnthropicClient) -> AnthropicProvider:
    config = ProviderConfig(
        name="claude",
        protocol="anthropic",
        default_model="claude-sonnet",
        tier="powerful",
    )
    provider = AnthropicProvider(config, api_key="x")
    provider._client = client  # type: ignore[assignment]
    return provider


@pytest.mark.asyncio
async def test_anthropic_empty_wrapup_returns_floor_not_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    messages = _AnthropicEmptyWrapupMessages()
    provider = _make_anthropic_provider(_AnthropicClient(messages))

    text, calls = await provider.complete_with_tools(
        user_text="search the web for me",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_failing_dispatcher,
    )

    assert text, "empty wrap-up must yield a non-empty floored answer, not blank"
    assert text.strip() != ""
    assert "web_search" in text.lower()
    assert messages.tools_seen[-1] is False
