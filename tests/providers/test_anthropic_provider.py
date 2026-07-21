"""Task 7 — ``AnthropicProvider`` honors per-model ``max_output_tokens`` overrides.

Mirrors ``test_complete_think_strip.py``'s ``test_output_cap_uses_per_model_override``
(Task 6, OpenAI sibling) for the Anthropic provider's ``stream()``/``complete()``
call sites. Also locks the accompanying bug fix: both call sites previously
invoked the local ``_max_tokens(kwargs)`` helper with NO ``default=`` argument,
so they always silently fell back to the hardcoded 4096 ceiling regardless of
``ProviderConfig.max_output_tokens`` — never exercising the real config value at
all. These tests assert the outbound ``max_tokens`` reflects the resolved
per-model/provider value (250000 in these fixtures), not 4096.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ModelOverride, ProviderConfig
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
    def __init__(self, text: str, model: str) -> None:
        self.content = [_Block(text)]
        self.usage = _Usage()
        self.model = model
        self.stop_reason = "end_turn"


class _ScriptedMessages:
    """Records each ``create(**kwargs)`` call, returning a canned response."""

    def __init__(self, text: str = "an answer") -> None:
        self._text = text
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _Resp:
        self.calls.append(kwargs)
        return _Resp(self._text, str(kwargs.get("model", "")))


class _FinalMessage:
    def __init__(self, model: str) -> None:
        self.usage = None
        self.model = model


class _StreamHandle:
    def __init__(self, text: str, model: str) -> None:
        self._text = text
        self._model = model

    async def __aenter__(self) -> _StreamHandle:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False

    async def _gen(self) -> Any:
        yield self._text

    @property
    def text_stream(self) -> Any:
        return self._gen()

    async def get_final_message(self) -> Any:
        return _FinalMessage(self._model)


class _ScriptedStreamMessages:
    """Records each ``stream(**kwargs)`` call, returning a canned stream handle."""

    def __init__(self, text: str = "an answer") -> None:
        self._text = text
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> _StreamHandle:
        self.calls.append(kwargs)
        return _StreamHandle(self._text, str(kwargs.get("model", "")))


class _FakeClient:
    def __init__(self, messages: Any) -> None:
        self.messages = messages


def _config_with_override() -> ProviderConfig:
    return ProviderConfig(
        name="anthropic",
        protocol="anthropic",
        default_model="claude-default",
        tiers=("powerful",),
        max_output_tokens=250000,
        models=(
            ModelOverride(name="claude-mini", tiers=("fast",), max_output_tokens=9000),
        ),
    )


async def test_complete_uses_provider_default_not_hardcoded_4096(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug fix: complete() must use the resolved config value, never the
    hardcoded 4096 fallback baked into ``_max_tokens``'s own default."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    messages = _ScriptedMessages()
    provider = AnthropicProvider(_config_with_override(), api_key="k")
    provider._client = _FakeClient(messages)  # type: ignore[assignment]

    await provider.complete([Message(role="user", content="hi")], model="claude-default")

    assert messages.calls[0]["max_tokens"] == 250000


async def test_complete_uses_per_model_override_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    messages = _ScriptedMessages()
    provider = AnthropicProvider(_config_with_override(), api_key="k")
    provider._client = _FakeClient(messages)  # type: ignore[assignment]

    await provider.complete([Message(role="user", content="hi")], model="claude-mini")

    assert messages.calls[0]["max_tokens"] == 9000


async def test_complete_explicit_kwarg_still_wins_over_resolved_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit caller-supplied max_tokens kwarg takes priority over the
    resolved default — unchanged _max_tokens() precedence, just re-asserted
    now that a real default flows through."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    messages = _ScriptedMessages()
    provider = AnthropicProvider(_config_with_override(), api_key="k")
    provider._client = _FakeClient(messages)  # type: ignore[assignment]

    await provider.complete(
        [Message(role="user", content="hi")], model="claude-mini", max_tokens=42
    )

    assert messages.calls[0]["max_tokens"] == 42


async def test_stream_uses_provider_default_not_hardcoded_4096(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    messages = _ScriptedStreamMessages()
    provider = AnthropicProvider(_config_with_override(), api_key="k")
    provider._client = _FakeClient(messages)  # type: ignore[assignment]

    async for _ in provider.stream([Message(role="user", content="hi")], model="claude-default"):
        pass

    assert messages.calls[0]["max_tokens"] == 250000


async def test_stream_uses_per_model_override_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    messages = _ScriptedStreamMessages()
    provider = AnthropicProvider(_config_with_override(), api_key="k")
    provider._client = _FakeClient(messages)  # type: ignore[assignment]

    async for _ in provider.stream([Message(role="user", content="hi")], model="claude-mini"):
        pass

    assert messages.calls[0]["max_tokens"] == 9000
