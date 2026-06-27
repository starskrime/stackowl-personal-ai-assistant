"""F-20 — ``AnthropicProvider.complete()`` must not wrap an empty/whitespace
generation as a silent success.

Parity with the OpenAI sibling: an empty content after extraction triggers ONE
retry as a cheap backstop. If the retry produces real text it is used; if it is
still empty the (non-silent, warning-logged) empty is returned rather than being
treated as a confident answer.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.providers.anthropic_provider import AnthropicProvider
from stackowl.providers.base import Message

pytestmark = pytest.mark.asyncio


class _Block:
    def __init__(self, text: str) -> None:
        self.text = text
        self.type = "text"


class _Usage:
    def __init__(self) -> None:
        self.input_tokens = 1
        self.output_tokens = 1


class _Resp:
    def __init__(self, text: str | None) -> None:
        self.content = [_Block(text)] if text is not None else []
        self.usage = _Usage()
        self.model = "claude-test"
        self.stop_reason = "end_turn"


class _ScriptedMessages:
    def __init__(self, texts: list[str | None]) -> None:
        self._texts = texts
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _Resp:
        self.calls.append(kwargs)
        idx = min(len(self.calls) - 1, len(self._texts) - 1)
        return _Resp(self._texts[idx])


class _FakeClient:
    def __init__(self, messages: _ScriptedMessages) -> None:
        self.messages = messages


def _make_provider(client: _FakeClient) -> AnthropicProvider:
    config = ProviderConfig(
        name="anthropic", protocol="anthropic", default_model="claude-test", tier="powerful",
    )
    provider = AnthropicProvider(config, api_key="k")
    provider._client = client  # type: ignore[assignment]
    return provider


async def test_complete_retries_once_on_empty_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    messages = _ScriptedMessages(["   ", "the real answer"])
    provider = _make_provider(_FakeClient(messages))

    result = await provider.complete([Message(role="user", content="hi")], model="")

    assert result.content == "the real answer"
    assert len(messages.calls) == 2  # retried exactly once on the empty draft


async def test_complete_no_retry_when_content_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    messages = _ScriptedMessages(["already good"])
    provider = _make_provider(_FakeClient(messages))

    result = await provider.complete([Message(role="user", content="hi")], model="")

    assert result.content == "already good"
    assert len(messages.calls) == 1  # no retry on a non-empty answer
