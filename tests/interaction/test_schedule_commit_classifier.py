"""Tests for :class:`ScheduleCommitClassifier` (overclaim trigger 4).

A fake fast-tier provider returns a canned verdict (real
``complete(messages, model, **kwargs)`` signature); commits_to_future_schedule maps:

* ``"COMMIT"`` → True; ``"NONE"`` → False.
* whitespace/punctuation tolerant: ``"COMMIT."`` / ``" none\n"``.
* garbage verdict (``"maybe"``) → False (fail-safe/NONE — the expensive
  direction is flooring a good answer, so ambiguity collapses to NONE).
* both tokens present → False (fail-safe/NONE).
* provider ``complete`` raising → False (fail-safe).
* provider call timing out → False (fail-safe).
* ``get_by_tier`` raising / no provider → False (fail-safe).
* empty response → False (fail-safe), no provider call.

Every case also asserts commits_to_future_schedule NEVER raises.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Literal

import pytest

from stackowl.interaction.schedule_commit_classifier import ScheduleCommitClassifier
from stackowl.providers.base import CompletionResult, Message, ModelProvider

_RESPONSE = "Sure, I'll ping you in 5 minutes to check!"


class _FakeProvider(ModelProvider):
    """Fast-tier provider stand-in honouring the real ModelProvider.complete sig."""

    def __init__(
        self,
        canned_verdict: str,
        *,
        raise_on_complete: Exception | None = None,
        hang_seconds: float | None = None,
    ) -> None:
        self._canned = canned_verdict
        self._raise = raise_on_complete
        self._hang_seconds = hang_seconds
        self.calls: list[list[Message]] = []

    @property
    def name(self) -> str:
        return "fake-fast"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object,
    ) -> CompletionResult:
        self.calls.append(list(messages))
        if self._hang_seconds is not None:
            await asyncio.sleep(self._hang_seconds)
        if self._raise is not None:
            raise self._raise
        return CompletionResult(
            content=self._canned,
            input_tokens=1,
            output_tokens=1,
            model="fake-model",
            provider_name=self.name,
            duration_ms=1.0,
        )

    async def stream(  # pragma: no cover — unused by the classifier
        self, messages: list[Message], model: str, **kwargs: object,
    ) -> AsyncIterator[str]:
        yield ""


class _FakeRegistry:
    """Minimal registry: get_by_tier returns a provided provider (or raises)."""

    def __init__(
        self,
        provider: ModelProvider | None = None,
        *,
        raise_on_get: Exception | None = None,
    ) -> None:
        self._provider = provider
        self._raise = raise_on_get
        self.tiers_requested: list[str] = []

    def get_by_tier(self, tier: str) -> ModelProvider:
        self.tiers_requested.append(tier)
        if self._raise is not None:
            raise self._raise
        assert self._provider is not None  # test wiring guarantee
        return self._provider


def _make(
    provider: ModelProvider | None = None,
    *,
    raise_on_get: Exception | None = None,
    timeout_s: float = 3.0,
) -> tuple[ScheduleCommitClassifier, _FakeRegistry]:
    registry = _FakeRegistry(provider, raise_on_get=raise_on_get)
    classifier = ScheduleCommitClassifier(registry, timeout_s=timeout_s)  # type: ignore[arg-type]
    return classifier, registry


@pytest.mark.asyncio
async def test_commit_verdict_true() -> None:
    classifier, registry = _make(_FakeProvider("COMMIT"))
    result = await classifier.commits_to_future_schedule(response=_RESPONSE)
    assert result is True
    assert registry.tiers_requested == ["fast"]


@pytest.mark.asyncio
async def test_none_verdict_false() -> None:
    classifier, _ = _make(_FakeProvider("NONE"))
    result = await classifier.commits_to_future_schedule(response="2 + 2 = 4.")
    assert result is False


@pytest.mark.asyncio
async def test_whitespace_and_punctuation_tolerant() -> None:
    classifier, _ = _make(_FakeProvider("COMMIT."))
    assert await classifier.commits_to_future_schedule(response=_RESPONSE) is True
    classifier2, _ = _make(_FakeProvider(" none\n"))
    assert await classifier2.commits_to_future_schedule(response=_RESPONSE) is False


@pytest.mark.asyncio
async def test_garbage_verdict_fails_safe_to_none() -> None:
    classifier, _ = _make(_FakeProvider("maybe"))
    assert await classifier.commits_to_future_schedule(response=_RESPONSE) is False


@pytest.mark.asyncio
async def test_both_tokens_fails_safe_to_none() -> None:
    classifier, _ = _make(_FakeProvider("COMMIT or NONE?"))
    assert await classifier.commits_to_future_schedule(response=_RESPONSE) is False


@pytest.mark.asyncio
async def test_provider_error_fails_safe_to_none() -> None:
    classifier, _ = _make(_FakeProvider("COMMIT", raise_on_complete=RuntimeError("boom")))
    assert await classifier.commits_to_future_schedule(response=_RESPONSE) is False


@pytest.mark.asyncio
async def test_provider_timeout_fails_safe_to_none() -> None:
    classifier, _ = _make(_FakeProvider("COMMIT", hang_seconds=5.0), timeout_s=0.05)
    assert await classifier.commits_to_future_schedule(response=_RESPONSE) is False


@pytest.mark.asyncio
async def test_no_provider_fails_safe_to_none() -> None:
    classifier, _ = _make(raise_on_get=RuntimeError("no providers configured"))
    assert await classifier.commits_to_future_schedule(response=_RESPONSE) is False


@pytest.mark.asyncio
async def test_empty_response_fails_safe_no_provider_call() -> None:
    classifier, registry = _make(_FakeProvider("COMMIT"))
    result = await classifier.commits_to_future_schedule(response="   ")
    assert result is False
    assert registry.tiers_requested == []  # no provider call for empty input
