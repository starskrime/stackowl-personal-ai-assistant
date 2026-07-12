"""disable_thinking → chat_template_kwargs passthrough on complete().

Classifier/structured callers pass ``disable_thinking=True`` to
``OpenAIProvider.complete`` so a reasoning fast-tier model emits its JSON/one-word
verdict WITHOUT a preceding ``<think>`` block (the live empty-verdict / 10s-timeout
bug). The provider must spread
``extra_body={"chat_template_kwargs": {"enable_thinking": False}}`` into the
underlying ``chat.completions.create(...)`` call ONLY when the caller opts in, and
must MERGE it with the ollama num_ctx window when both apply.

Reuses the fake-client pattern from test_ollama_num_ctx / test_phaseF_max_out.
"""
from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.providers import model_window as mw
from stackowl.providers.base import Message
from stackowl.providers.openai_provider import OpenAIProvider

_OLLAMA_BASE_URL = "http://x:11434/v1"
_OPENAI_BASE_URL = "https://api.openai.com/v1"
_MODEL = "qwen3.5:9b"


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content
        self.tool_calls = None


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(_FakeMessage(content))]
        self.model = _MODEL
        self.usage = None


class _CapturingCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(dict(kwargs))
        return _FakeResponse("DONE")


class _FakeChat:
    def __init__(self, completions: Any) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: Any) -> None:
        self.chat = _FakeChat(completions)


def _make_provider(base_url: str, name: str) -> OpenAIProvider:
    config = ProviderConfig(
        name=name, protocol="openai", base_url=base_url,
        default_model=_MODEL, tier="local",
    )
    return OpenAIProvider(config, api_key="")


@pytest.fixture(autouse=True)
def _clear_window_cache() -> Any:
    mw._WINDOW_CACHE.clear()
    yield
    mw._WINDOW_CACHE.clear()


@pytest.mark.asyncio
async def test_disable_thinking_sends_chat_template_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """complete(disable_thinking=True) → create() gets enable_thinking=False."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _CapturingCompletions()
    provider = _make_provider(_OPENAI_BASE_URL, name="fast")
    provider._client = _FakeClient(completions)  # type: ignore[assignment]

    await provider.complete([Message(role="user", content="hi")], model="", disable_thinking=True)

    assert len(completions.calls) == 1
    assert completions.calls[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


@pytest.mark.asyncio
async def test_without_disable_thinking_no_extra_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default (no disable_thinking) on a non-ollama provider → no extra_body at all
    (byte-identical to prior behaviour)."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _CapturingCompletions()
    provider = _make_provider(_OPENAI_BASE_URL, name="fast")
    provider._client = _FakeClient(completions)  # type: ignore[assignment]

    await provider.complete([Message(role="user", content="hi")], model="")

    assert "extra_body" not in completions.calls[0]


@pytest.mark.asyncio
async def test_disable_thinking_merges_with_ollama_num_ctx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ollama URL + cached window + disable_thinking → BOTH hints survive in one
    extra_body (merge, not clobber)."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    mw._WINDOW_CACHE[("ollama", _MODEL)] = 12000
    completions = _CapturingCompletions()
    provider = _make_provider(_OLLAMA_BASE_URL, name="ollama")
    provider._client = _FakeClient(completions)  # type: ignore[assignment]

    await provider.complete([Message(role="user", content="hi")], model="", disable_thinking=True)

    assert completions.calls[0]["extra_body"] == {
        "options": {"num_ctx": 12000},
        "chat_template_kwargs": {"enable_thinking": False},
    }
