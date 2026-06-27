"""F-23 — ``GeminiProvider`` must not coerce a blocked/empty generation into a
silent empty success.

``response.text or ""`` returns "" both for a transient empty generation AND for
a safety/recitation BLOCK. The fix distinguishes them: a confirmed block surfaces
honestly (ProviderError so the gateway floors), and a plain empty generation gets
ONE retry as a cheap backstop (parity with the other providers).
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import ProviderError
from stackowl.providers.base import Message
from stackowl.providers.gemini_provider import GeminiProvider

pytestmark = pytest.mark.asyncio


class _FinishReason:
    def __init__(self, name: str) -> None:
        self.name = name


class _Candidate:
    def __init__(self, finish_reason: str | None) -> None:
        self.finish_reason = _FinishReason(finish_reason) if finish_reason else None


class _Usage:
    def __init__(self) -> None:
        self.prompt_token_count = 1
        self.candidates_token_count = 1


class _Resp:
    def __init__(self, text: str | None, finish_reason: str | None = "STOP") -> None:
        self._text = text
        self.candidates = [_Candidate(finish_reason)]
        self.prompt_feedback = None
        self.usage_metadata = _Usage()

    @property
    def text(self) -> str | None:
        return self._text


class _ScriptedModels:
    def __init__(self, responses: list[_Resp]) -> None:
        self._responses = responses
        self.calls = 0

    async def generate_content(self, **kwargs: Any) -> _Resp:
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]


class _FakeAio:
    def __init__(self, models: _ScriptedModels) -> None:
        self.models = models


class _FakeClient:
    def __init__(self, models: _ScriptedModels) -> None:
        self.aio = _FakeAio(models)


def _make_provider(client: _FakeClient) -> GeminiProvider:
    config = ProviderConfig(
        name="gemini", protocol="gemini", default_model="gemini-test", tier="standard",
    )
    provider = GeminiProvider(config, api_key="k")
    provider._client = client  # type: ignore[assignment]
    return provider


async def test_complete_surfaces_safety_block_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    models = _ScriptedModels([_Resp(None, finish_reason="SAFETY")])
    provider = _make_provider(_FakeClient(models))
    with pytest.raises(ProviderError):
        await provider.complete([Message(role="user", content="hi")], model="")


async def test_complete_retries_once_on_plain_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    # First a plain empty (normal STOP, no block), then real text on retry.
    models = _ScriptedModels([_Resp("", finish_reason="STOP"), _Resp("recovered", finish_reason="STOP")])
    provider = _make_provider(_FakeClient(models))

    result = await provider.complete([Message(role="user", content="hi")], model="")

    assert result.content == "recovered"
    assert models.calls == 2  # retried exactly once


async def test_complete_no_retry_when_text_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    models = _ScriptedModels([_Resp("direct answer", finish_reason="STOP")])
    provider = _make_provider(_FakeClient(models))

    result = await provider.complete([Message(role="user", content="hi")], model="")

    assert result.content == "direct answer"
    assert models.calls == 1
