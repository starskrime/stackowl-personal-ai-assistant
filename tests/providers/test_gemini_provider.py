"""Task 7 — ``GeminiProvider`` honors per-model ``max_output_tokens`` overrides.

Mirrors ``test_complete_think_strip.py``'s ``test_output_cap_uses_per_model_override``
(Task 6, OpenAI sibling) for the Gemini provider's ``stream()``/``complete()``
call sites. Also locks the accompanying bug fix: both call sites previously
invoked the local ``_max_tokens(kwargs)`` helper with NO ``default=`` argument,
so they always silently fell back to the hardcoded 4096 ceiling regardless of
``ProviderConfig.max_output_tokens`` — never exercising the real config value
at all. These tests assert the outbound ``max_output_tokens`` reflects the
resolved per-model/provider value (250000 in these fixtures), not 4096.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ModelOverride, ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.providers.base import Message
from stackowl.providers.gemini_provider import GeminiProvider

pytestmark = pytest.mark.asyncio


class _FinishReason:
    def __init__(self, name: str) -> None:
        self.name = name


class _Candidate:
    def __init__(self, finish_reason: str = "STOP") -> None:
        self.finish_reason = _FinishReason(finish_reason)


class _Usage:
    def __init__(self) -> None:
        self.prompt_token_count = 1
        self.candidates_token_count = 1


class _Resp:
    def __init__(self, text: str = "an answer") -> None:
        self._text = text
        self.candidates = [_Candidate()]
        self.prompt_feedback = None
        self.usage_metadata = _Usage()

    @property
    def text(self) -> str:
        return self._text


class _ScriptedModels:
    """Records each ``generate_content(**kwargs)`` call, returning a canned response."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> _Resp:
        self.calls.append(kwargs)
        return _Resp()


class _Chunk:
    def __init__(self, text: str = "an answer") -> None:
        self.text = text
        self.usage_metadata = None


class _ScriptedStreamModels:
    """Records each ``generate_content_stream(**kwargs)`` call, returning one chunk."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_content_stream(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)

        async def _gen() -> Any:
            yield _Chunk()

        return _gen()


class _FakeAio:
    def __init__(self, models: Any) -> None:
        self.models = models


class _FakeClient:
    def __init__(self, models: Any) -> None:
        self.aio = _FakeAio(models)


def _config_with_override() -> ProviderConfig:
    return ProviderConfig(
        name="gemini",
        protocol="gemini",
        default_model="gemini-default",
        tiers=("standard",),
        max_output_tokens=250000,
        models=(
            ModelOverride(name="gemini-mini", tiers=("fast",), max_output_tokens=9000),
        ),
    )


def _make_provider(client: _FakeClient) -> GeminiProvider:
    provider = GeminiProvider.__new__(GeminiProvider)  # bypass genai.Client construction
    provider._name = "gemini"  # type: ignore[attr-defined]
    provider._config = _config_with_override()  # type: ignore[attr-defined]
    provider._client = client  # type: ignore[attr-defined]
    return provider


async def test_complete_uses_provider_default_not_hardcoded_4096(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug fix: complete() must use the resolved config value, never the
    hardcoded 4096 fallback baked into ``_max_tokens``'s own default."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    models = _ScriptedModels()
    provider = _make_provider(_FakeClient(models))

    await provider.complete([Message(role="user", content="hi")], model="gemini-default")

    assert models.calls[0]["config"].max_output_tokens == 250000


async def test_complete_uses_per_model_override_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    models = _ScriptedModels()
    provider = _make_provider(_FakeClient(models))

    await provider.complete([Message(role="user", content="hi")], model="gemini-mini")

    assert models.calls[0]["config"].max_output_tokens == 9000


async def test_complete_explicit_kwarg_still_wins_over_resolved_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit caller-supplied max_tokens kwarg takes priority over the
    resolved default — unchanged _max_tokens() precedence, just re-asserted
    now that a real default flows through."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    models = _ScriptedModels()
    provider = _make_provider(_FakeClient(models))

    await provider.complete(
        [Message(role="user", content="hi")], model="gemini-mini", max_tokens=42
    )

    assert models.calls[0]["config"].max_output_tokens == 42


async def test_stream_uses_provider_default_not_hardcoded_4096(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    models = _ScriptedStreamModels()
    provider = _make_provider(_FakeClient(models))

    async for _ in provider.stream([Message(role="user", content="hi")], model="gemini-default"):
        pass

    assert models.calls[0]["config"].max_output_tokens == 250000


async def test_stream_uses_per_model_override_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    models = _ScriptedStreamModels()
    provider = _make_provider(_FakeClient(models))

    async for _ in provider.stream([Message(role="user", content="hi")], model="gemini-mini"):
        pass

    assert models.calls[0]["config"].max_output_tokens == 9000
