"""Tests for :class:`FeedbackClassifier`.

A fake fast-tier provider returns a canned JSON verdict (real
``complete(messages, model, **kwargs)`` signature). We assert the classifier
maps a SCRIPTED model verdict to the right :class:`FeedbackResult` — we never
call a real model, so these prove the parsing/abstain/fail-open logic, and that
classification follows the VERDICT (not an English wordlist).

Cases:
* "I like this, keep it"          → positive (verdict-driven).
* "no you broke it again"         → negative.
* "good content but lose the *"   → MIXED: positive/content + negative/format.
* "try again"                     → neutral/retry, not abstain.
* low-confidence verdict          → abstain flag set.
* provider error / get_by_tier    → abstain, never raises (fail-open).
* a non-English positive verdict  → positive (proves no English gate).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Literal

import pytest

from stackowl.interaction.feedback_classifier import (
    FeedbackClassifier,
    FeedbackResult,
)
from stackowl.providers.base import CompletionResult, Message, ModelProvider

_AGENT_MSG = "Here are today's AI headlines:\n* Model X ships\n* Lab Y raises funds"


class _FakeProvider(ModelProvider):
    """Fast-tier stand-in honouring the real ModelProvider.complete signature."""

    def __init__(
        self,
        canned: str,
        *,
        raise_on_complete: Exception | None = None,
        hang_seconds: float | None = None,
    ) -> None:
        self._canned = canned
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
    """Minimal registry: get_by_tier returns the provider (or raises)."""

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


def _verdict(signals: list[dict[str, object]], referent: str = "last") -> str:
    return json.dumps({"signals": signals, "referent": referent})


def _make(
    provider: ModelProvider | None = None,
    *,
    raise_on_get: Exception | None = None,
    abstain_threshold: float = 0.5,
    timeout_s: float = 4.0,
) -> tuple[FeedbackClassifier, _FakeRegistry]:
    registry = _FakeRegistry(provider, raise_on_get=raise_on_get)
    classifier = FeedbackClassifier(
        registry,  # type: ignore[arg-type]
        timeout_s=timeout_s,
        abstain_threshold=abstain_threshold,
    )
    return classifier, registry


async def _classify(classifier: FeedbackClassifier, message: str) -> FeedbackResult:
    return await classifier.classify(
        user_message=message, last_agent_message=_AGENT_MSG,
    )


# --------------------------------------------------------------- (a) positive


@pytest.mark.asyncio
async def test_positive_keep_it() -> None:
    verdict = _verdict([{"polarity": "positive", "aspect": "overall", "confidence": 0.95}])
    classifier, registry = _make(_FakeProvider(verdict))
    out = await _classify(classifier, "I like this, keep it")
    assert out.abstain is False
    assert out.primary.polarity == "positive"
    assert out.referent == "last"
    assert registry.tiers_requested == ["fast"]  # resolved the FAST tier


# --------------------------------------------------------------- (b) negative


@pytest.mark.asyncio
async def test_negative_broke_it() -> None:
    verdict = _verdict([{"polarity": "negative", "aspect": "overall", "confidence": 0.9}])
    classifier, _ = _make(_FakeProvider(verdict))
    out = await _classify(classifier, "no you broke it again")
    assert out.abstain is False
    assert out.primary.polarity == "negative"


# ------------------------------------------------------------------ (c) mixed


@pytest.mark.asyncio
async def test_mixed_content_positive_format_negative() -> None:
    verdict = _verdict(
        [
            {"polarity": "positive", "aspect": "content", "confidence": 0.9},
            {"polarity": "negative", "aspect": "format", "confidence": 0.85},
        ]
    )
    classifier, _ = _make(_FakeProvider(verdict))
    out = await _classify(classifier, "good content but lose the asterisks")
    assert out.abstain is False
    by_aspect = {s.aspect: s.polarity for s in out.signals}
    assert by_aspect == {"content": "positive", "format": "negative"}


# -------------------------------------------------------------- (d) neutral


@pytest.mark.asyncio
async def test_neutral_retry_is_not_abstain() -> None:
    verdict = _verdict([{"polarity": "neutral", "aspect": "overall", "confidence": 0.9}])
    classifier, _ = _make(_FakeProvider(verdict))
    out = await _classify(classifier, "try again")
    assert out.abstain is False  # confident neutral — not the same as abstain
    assert out.primary.polarity == "neutral"


# -------------------------------------------------------------- (e) abstain


@pytest.mark.asyncio
async def test_low_confidence_sets_abstain() -> None:
    verdict = _verdict([{"polarity": "positive", "aspect": "overall", "confidence": 0.2}])
    classifier, _ = _make(_FakeProvider(verdict), abstain_threshold=0.5)
    out = await _classify(classifier, "hmm ok i guess")
    assert out.abstain is True  # below threshold → ask, don't guess


# ----------------------------------------------------------- (f) fail-open


@pytest.mark.asyncio
async def test_provider_error_abstains_without_crash() -> None:
    classifier, _ = _make(_FakeProvider("", raise_on_complete=RuntimeError("boom")))
    out = await _classify(classifier, "I like this")
    assert out.abstain is True
    assert out.reason == "provider_error"
    assert out.primary.polarity == "neutral"


@pytest.mark.asyncio
async def test_missing_provider_abstains() -> None:
    classifier, _ = _make(raise_on_get=RuntimeError("no providers"))
    out = await _classify(classifier, "I like this")
    assert out.abstain is True
    assert out.reason == "no_provider"


@pytest.mark.asyncio
async def test_unparseable_verdict_abstains() -> None:
    classifier, _ = _make(_FakeProvider("not json at all"))
    out = await _classify(classifier, "I like this")
    assert out.abstain is True
    assert out.reason == "unparseable"


@pytest.mark.asyncio
async def test_empty_message_abstains_without_provider_call() -> None:
    provider = _FakeProvider(_verdict([]))
    classifier, registry = _make(provider)
    out = await classifier.classify(user_message="   ", last_agent_message=_AGENT_MSG)
    assert out.abstain is True
    assert out.reason == "empty_message"
    assert provider.calls == []  # short-circuited before any provider call
    assert registry.tiers_requested == []


# --------------------------------------------------- (g) non-English positive


@pytest.mark.asyncio
async def test_non_english_positive_classifies_by_verdict() -> None:
    # "me gusta, déjalo así" (Spanish) — no English token anywhere. The verdict
    # alone drives the result, proving there is no English-wordlist gate.
    verdict = _verdict([{"polarity": "positive", "aspect": "overall", "confidence": 0.92}])
    classifier, _ = _make(_FakeProvider(verdict))
    out = await _classify(classifier, "me gusta, déjalo así")
    assert out.abstain is False
    assert out.primary.polarity == "positive"


# ---------------------------------------------- extra: invalid signal dropped


@pytest.mark.asyncio
async def test_invalid_enum_signal_is_dropped_not_coerced() -> None:
    # One bogus polarity (dropped) + one valid signal (kept) — an unknown token is
    # never coerced into a default polarity (that would be the wrong-capture bug).
    verdict = _verdict(
        [
            {"polarity": "amazing", "aspect": "format", "confidence": 0.9},
            {"polarity": "negative", "aspect": "format", "confidence": 0.8},
        ]
    )
    classifier, _ = _make(_FakeProvider(verdict))
    out = await _classify(classifier, "ditch the bold")
    assert len(out.signals) == 1
    assert out.primary.polarity == "negative"


@pytest.mark.asyncio
async def test_all_invalid_signals_abstain() -> None:
    verdict = _verdict([{"polarity": "amazing", "aspect": "vibes", "confidence": 0.9}])
    classifier, _ = _make(_FakeProvider(verdict))
    out = await _classify(classifier, "whatever")
    assert out.abstain is True
    assert out.reason == "no_valid_signal"
