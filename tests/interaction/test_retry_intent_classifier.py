"""Tests for RetryIntentClassifier — mirrors FeedbackClassifier's real
provider-call shape: ``provider_registry.get_by_tier("fast")`` returning a
:class:`~stackowl.providers.base.ModelProvider` whose ``complete(...)``
returns a :class:`~stackowl.providers.base.CompletionResult`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.interaction.retry_intent_classifier import RetryIntentClassifier
from stackowl.providers.base import CompletionResult


def _completion(content: str) -> CompletionResult:
    return CompletionResult(
        content=content,
        input_tokens=0,
        output_tokens=0,
        model="fast-test",
        provider_name="test",
        duration_ms=1.0,
    )


def _registry_with(raw_response: str) -> MagicMock:
    provider_registry = MagicMock()
    fake_provider = MagicMock()
    fake_provider.complete = AsyncMock(return_value=_completion(raw_response))
    provider_registry.get_by_tier = MagicMock(return_value=fake_provider)
    return provider_registry


@pytest.mark.asyncio
async def test_classify_retry_phrase_returns_true():
    provider_registry = _registry_with('{"is_retry": true, "confidence": 0.9}')

    classifier = RetryIntentClassifier(provider_registry)
    result = await classifier.classify(
        user_message="do it again", prior_goal="prepare me for the interview",
    )

    assert result is True
    provider_registry.get_by_tier.assert_called_once_with("fast")


@pytest.mark.asyncio
async def test_classify_unrelated_message_returns_false():
    provider_registry = _registry_with('{"is_retry": false, "confidence": 0.95}')

    classifier = RetryIntentClassifier(provider_registry)
    result = await classifier.classify(
        user_message="what's the weather", prior_goal="prepare me for the interview",
    )

    assert result is False


@pytest.mark.asyncio
async def test_classify_below_abstain_threshold_returns_false():
    provider_registry = _registry_with('{"is_retry": true, "confidence": 0.2}')

    classifier = RetryIntentClassifier(provider_registry)
    result = await classifier.classify(
        user_message="hmm maybe", prior_goal="prepare me for the interview",
    )

    assert result is False


@pytest.mark.asyncio
async def test_classify_unparseable_json_fails_open_to_false():
    provider_registry = _registry_with("not json at all")

    classifier = RetryIntentClassifier(provider_registry)
    result = await classifier.classify(
        user_message="do it again", prior_goal="prepare me for the interview",
    )

    assert result is False


@pytest.mark.asyncio
async def test_classify_provider_error_fails_open_to_false():
    provider_registry = MagicMock()
    fake_provider = MagicMock()
    fake_provider.complete = AsyncMock(side_effect=RuntimeError("boom"))
    provider_registry.get_by_tier = MagicMock(return_value=fake_provider)

    classifier = RetryIntentClassifier(provider_registry)
    result = await classifier.classify(
        user_message="do it again", prior_goal="prepare me for the interview",
    )

    assert result is False


@pytest.mark.asyncio
async def test_classify_no_provider_fails_open_to_false():
    provider_registry = MagicMock()
    provider_registry.get_by_tier = MagicMock(side_effect=RuntimeError("no fast provider"))

    classifier = RetryIntentClassifier(provider_registry)
    result = await classifier.classify(
        user_message="do it again", prior_goal="prepare me for the interview",
    )

    assert result is False
