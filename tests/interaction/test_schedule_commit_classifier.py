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

from stackowl.interaction.schedule_commit_classifier import (
    _SYSTEM_PROMPT,
    ScheduleCommitClassifier,
)
from stackowl.providers.base import CompletionResult, Message, ModelProvider

# Live incident (2026-07-22): this EXACT draft (a status report of already-
# scheduled jobs, not a new promise) got a false-positive COMMIT verdict,
# floored, and delivered a confusing "I didn't actually schedule anything"
# non-sequitur to the user asking about it. Captured verbatim from
# stackowl.jsonl's response_snippet field after adding that logging.
_REAL_FALSE_POSITIVE_DRAFT = (
    "Understood.\n\nHere is the current status of the **Secretary's** "
    "scheduled tasks:\n\n### ✅ Active Scheduled Tasks\n1.  **Headhunter**\n"
    "    *   **Goal:** Find Senior/Lead/Staff Software Engineer jobs in "
    "Plano, TX.\n    *   **Schedule:** Every 3 hours (8 AM – 8 PM CDT).\n"
    "    *   **Status:** Active.\n\n2.  **Brain (Me)**\n"
    "    *   **Goal:** Daily brainstorming and AI trends.\n"
    "    *   **Schedule:** Daily at 9:00 A"
)

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
        self.models: list[str] = []

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
        self.models.append(model)
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
    """Minimal registry: get_by_tier returns a provided (provider,
    model) pair (or raises)."""

    def __init__(
        self,
        provider: ModelProvider | None = None,
        *,
        model: str = "fake-fast-model",
        raise_on_get: Exception | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._raise = raise_on_get
        self.tiers_requested: list[str] = []

    def get_by_tier(self, tier: str) -> tuple[ModelProvider, str]:
        self.tiers_requested.append(tier)
        if self._raise is not None:
            raise self._raise
        assert self._provider is not None  # test wiring guarantee
        return self._provider, self._model


def _make(
    provider: ModelProvider | None = None,
    *,
    model: str = "fake-fast-model",
    raise_on_get: Exception | None = None,
    timeout_s: float = 3.0,
) -> tuple[ScheduleCommitClassifier, _FakeRegistry]:
    registry = _FakeRegistry(provider, model=model, raise_on_get=raise_on_get)
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


@pytest.mark.asyncio
async def test_resolved_model_reaches_provider_complete() -> None:
    """The (provider, model) pair resolved from get_by_tier must be
    threaded into provider.complete(..., model=...) — not hardcoded to ""."""
    provider = _FakeProvider("COMMIT")
    classifier, _ = _make(provider, model="qwen-schedule-commit-v2")
    await classifier.commits_to_future_schedule(response=_RESPONSE)
    assert provider.models == ["qwen-schedule-commit-v2"]


def test_prompt_distinguishes_reporting_existing_schedules_from_new_promises() -> None:
    """Live incident 2026-07-22 regression guard: the system prompt must keep
    an explicit instruction that REPORTING/LISTING already-scheduled work is
    NOT a new commitment — else this exact false positive (a status summary
    of existing jobs, saturated with the words "Scheduled"/"Schedule:"/
    "Active", misread as a brand-new promise) can silently come back if the
    prompt is ever edited without this guard in mind. Can't unit-test the
    real model's verdict here (these tests mock the provider), so this pins
    the INSTRUCTION'S presence instead."""
    lowered = _SYSTEM_PROMPT.lower()
    assert "already exists" in lowered or "report" in lowered
    assert "first time" in lowered or "new" in lowered


@pytest.mark.asyncio
async def test_real_false_positive_draft_reaches_provider_intact() -> None:
    """Plumbing check using the EXACT draft text that caused the live
    incident (see _REAL_FALSE_POSITIVE_DRAFT) — confirms it reaches
    provider.complete() intact (within _MAX_RESPONSE_CHARS) and the fixed
    prompt's instructions are present in the system message sent alongside
    it. A real fast-tier model should now verdict NONE on this input; anyone
    re-validating the prompt against a live model should use this exact
    fixture."""
    provider = _FakeProvider("NONE")
    classifier, _ = _make(provider)
    result = await classifier.commits_to_future_schedule(
        response=_REAL_FALSE_POSITIVE_DRAFT
    )
    assert result is False
    sent_system_message = provider.calls[0][0]
    assert sent_system_message.role == "system"
    assert "already exists" in sent_system_message.content.lower() or (
        "report" in sent_system_message.content.lower()
    )
    sent_user_message = provider.calls[0][1]
    assert "Headhunter" in sent_user_message.content
