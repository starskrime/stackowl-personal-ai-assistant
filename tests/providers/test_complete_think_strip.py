"""Empty reasoning-model output robustness for ``OpenAIProvider.complete()``.

Live break (2026-06-23): the local qwen3.5 reasoning model spent its whole 4096
output-token budget inside an un-stripped ``<think>`` block, so ``complete()``
returned EMPTY content. That empty string crashed the fact extractor
(``FactExtractionParseError``) and fooled the persistence judge into "failing
open" — shipping an unvetted draft.

These tests drive three guarantees on the plain ``complete()`` path:
  1. ``<think>…</think>`` reasoning blocks are stripped from the answer.
  2. Empty-after-strip (truncated mid-thinking) triggers ONE retry with thinking
     disabled.
  3. ``disable_thinking=True`` forwards the no-think knob to the request and does
     not retry (thinking is already off).
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.providers.base import Message
from stackowl.providers.openai_provider import OpenAIProvider, strip_think


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content
        self.tool_calls = None


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeMessage(content)
        self.finish_reason = "length"


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeChoice(content)]
        self.model = "qwen3.5:2b"
        self.usage = None


class _ScriptedCompletions:
    """Returns queued contents in order, recording each call's kwargs."""

    def __init__(self, contents: list[str | None]) -> None:
        self._contents = contents
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        idx = min(len(self.calls) - 1, len(self._contents) - 1)
        return _FakeResponse(self._contents[idx])


class _FakeChat:
    def __init__(self, completions: _ScriptedCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: _ScriptedCompletions) -> None:
        self.chat = _FakeChat(completions)


def _make_provider(client: _FakeClient) -> OpenAIProvider:
    config = ProviderConfig(
        name="ollama",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="qwen3.5:2b",
        tier="fast",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = client  # type: ignore[assignment]
    return provider


def test_strip_think_removes_closed_block() -> None:
    assert strip_think("<think>reasoning here</think>\nThe answer") == "The answer"


def test_strip_think_drops_unclosed_truncated_block() -> None:
    # Truncated mid-thinking (no closing tag) ⇒ everything from <think> is reasoning.
    assert strip_think("prefix<think>still reasoning when the cap hit") == "prefix"
    assert strip_think("<think>only thinking, cut off") == ""


@pytest.mark.asyncio
async def test_complete_strips_think_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ScriptedCompletions(["<think>deliberating</think>\nfinal answer"])
    provider = _make_provider(_FakeClient(completions))

    result = await provider.complete([Message(role="user", content="hi")], model="")

    assert result.content == "final answer"
    assert len(completions.calls) == 1  # no retry needed


@pytest.mark.asyncio
async def test_complete_retries_once_on_empty_after_strip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    # 1st call: all thinking, truncated → empty after strip. 2nd: real JSON.
    completions = _ScriptedCompletions(
        ["<think>thinking forever, cut off at the cap", '[{"fact":"x"}]']
    )
    provider = _make_provider(_FakeClient(completions))

    result = await provider.complete([Message(role="user", content="hi")], model="")

    assert result.content == '[{"fact":"x"}]'
    assert len(completions.calls) == 2  # retried exactly once
    # The retry carried the no-think knob.
    retry_body = completions.calls[1].get("extra_body", {})
    assert retry_body.get("chat_template_kwargs", {}).get("enable_thinking") is False


@pytest.mark.asyncio
async def test_complete_disable_thinking_forwards_knob_and_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    # Even though the (only) response is empty, disable_thinking=True must NOT retry.
    completions = _ScriptedCompletions([""])
    provider = _make_provider(_FakeClient(completions))

    result = await provider.complete(
        [Message(role="user", content="hi")], model="", disable_thinking=True
    )

    assert result.content == ""
    assert len(completions.calls) == 1  # no retry when thinking already disabled
    body = completions.calls[0].get("extra_body", {})
    assert body.get("chat_template_kwargs", {}).get("enable_thinking") is False
