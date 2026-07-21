"""Tests for :class:`ClarifyIntentClassifier`.

A fake fast-tier provider returns a canned verdict (real
``complete(messages, model, **kwargs)`` signature); is_answer maps:

* ``"ANSWER"`` → True; ``"NEW"`` → False; ``"new request"`` → False.
* whitespace/punctuation tolerant: ``"ANSWER."`` / ``" answer\n"`` → True.
* garbage verdict (``"maybe"``) → True (fail-safe).
* provider ``complete`` raising → True (fail-safe).
* ``get_by_tier`` raising / no provider → True (fail-safe).
* empty message → True (fail-safe), no provider call.

Every case also asserts is_answer NEVER raises.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Literal

import pytest

from stackowl.interaction.intent_classifier import (
    AnswerVerdict,
    ClarifyIntentClassifier,
)
from stackowl.providers.base import CompletionResult, Message, ModelProvider

_QUESTION = "Which environment should I deploy to?"
_CHOICES = ("staging", "production")
_MESSAGE = "production"


class _FakeProvider(ModelProvider):
    """Fast-tier provider stand-in honouring the real ModelProvider.complete sig.

    Returns ``canned_verdict`` from complete(); if ``raise_on_complete`` is set,
    complete() raises it. Records the messages it was called with for assertion.
    """

    def __init__(
        self,
        canned_verdict: str = "ANSWER",
        *,
        raise_on_complete: Exception | None = None,
        hang_seconds: float | None = None,
    ) -> None:
        self._verdict = canned_verdict
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
            content=self._verdict,
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
    """Minimal registry: get_by_tier_and_model returns a provided (provider,
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

    def get_by_tier_and_model(self, tier: str) -> tuple[ModelProvider, str]:
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
) -> tuple[ClarifyIntentClassifier, _FakeRegistry]:
    registry = _FakeRegistry(provider, model=model, raise_on_get=raise_on_get)
    classifier = ClarifyIntentClassifier(registry, timeout_s=timeout_s)  # type: ignore[arg-type]
    return classifier, registry


# ----------------------------------------------------------------- verdict map


@pytest.mark.asyncio
async def test_answer_verdict_is_true() -> None:
    classifier, registry = _make(_FakeProvider("ANSWER"))
    out = await classifier.is_answer(
        question=_QUESTION, choices=_CHOICES, message=_MESSAGE,
    )
    assert out is True
    # Resolved the FAST tier.
    assert registry.tiers_requested == ["fast"]


@pytest.mark.asyncio
async def test_new_verdict_is_false() -> None:
    classifier, _ = _make(_FakeProvider("NEW"))
    out = await classifier.is_answer(
        question=_QUESTION, choices=_CHOICES, message="actually, what's the weather?",
    )
    assert out is False


@pytest.mark.asyncio
async def test_new_request_phrase_is_false() -> None:
    classifier, _ = _make(_FakeProvider("new request"))
    out = await classifier.is_answer(
        question=_QUESTION, choices=_CHOICES, message="show me my calendar",
    )
    assert out is False


@pytest.mark.asyncio
async def test_answer_with_trailing_punctuation_is_true() -> None:
    classifier, _ = _make(_FakeProvider("ANSWER."))
    out = await classifier.is_answer(
        question=_QUESTION, choices=_CHOICES, message=_MESSAGE,
    )
    assert out is True


@pytest.mark.asyncio
async def test_answer_with_surrounding_whitespace_is_true() -> None:
    classifier, _ = _make(_FakeProvider(" answer\n"))
    out = await classifier.is_answer(
        question=_QUESTION, choices=_CHOICES, message=_MESSAGE,
    )
    assert out is True


# ------------------------------------------------------------------ fail-safe


@pytest.mark.asyncio
async def test_garbage_verdict_fail_safe_true() -> None:
    classifier, _ = _make(_FakeProvider("maybe"))
    out = await classifier.is_answer(
        question=_QUESTION, choices=_CHOICES, message=_MESSAGE,
    )
    assert out is True


@pytest.mark.asyncio
async def test_provider_raising_fail_safe_true() -> None:
    classifier, _ = _make(
        _FakeProvider(raise_on_complete=RuntimeError("boom")),
    )
    out = await classifier.is_answer(
        question=_QUESTION, choices=_CHOICES, message=_MESSAGE,
    )
    assert out is True


@pytest.mark.asyncio
async def test_get_by_tier_raising_fail_safe_true() -> None:
    classifier, registry = _make(raise_on_get=RuntimeError("no providers"))
    out = await classifier.is_answer(
        question=_QUESTION, choices=_CHOICES, message=_MESSAGE,
    )
    assert out is True
    assert registry.tiers_requested == ["fast"]


@pytest.mark.asyncio
async def test_empty_message_fail_safe_true_without_provider_call() -> None:
    provider = _FakeProvider("NEW")  # would say NEW if ever called
    classifier, registry = _make(provider)
    out = await classifier.is_answer(
        question=_QUESTION, choices=_CHOICES, message="   ",
    )
    assert out is True
    # Short-circuited before any provider resolution / call.
    assert registry.tiers_requested == []
    assert provider.calls == []


# -------------------------------------------------------------- never raises


@pytest.mark.asyncio
async def test_is_answer_never_raises_across_inputs() -> None:
    cases = [
        _make(_FakeProvider("ANSWER")),
        _make(_FakeProvider("NEW")),
        _make(_FakeProvider("maybe")),
        _make(_FakeProvider(raise_on_complete=ValueError("x"))),
        _make(raise_on_get=RuntimeError("none")),
    ]
    for classifier, _ in cases:
        out = await classifier.is_answer(
            question=_QUESTION, choices=_CHOICES, message=_MESSAGE,
        )
        assert isinstance(out, bool)


@pytest.mark.asyncio
async def test_no_choices_still_classifies() -> None:
    """An empty choices tuple is fine — the LLM classifies on question+reply alone."""
    classifier, _ = _make(_FakeProvider("ANSWER"))
    out = await classifier.is_answer(
        question="Free text question?", choices=(), message="some answer",
    )
    assert out is True


# --------------------------------------------- MAJOR-1: token-order robustness


@pytest.mark.asyncio
async def test_verbose_new_verdict_with_answer_token_is_false() -> None:
    """A verbose NEW verdict that also contains 'answer' must NOT be swallowed.

    Regression for token precedence: "NEW — this does not answer the question"
    contains BOTH tokens, but the leading token (NEW) wins, so the pivot is preserved
    instead of being misclassified as an answer (the old "answer"-first bug).
    """
    classifier, _ = _make(
        _FakeProvider("NEW — this does not answer the question"),
    )
    out = await classifier.is_answer(
        question=_QUESTION, choices=_CHOICES, message="actually, what's the weather?",
    )
    assert out is False


@pytest.mark.asyncio
async def test_answer_alone_is_true() -> None:
    classifier, _ = _make(_FakeProvider("ANSWER"))
    out = await classifier.is_answer(
        question=_QUESTION, choices=_CHOICES, message=_MESSAGE,
    )
    assert out is True


@pytest.mark.asyncio
async def test_new_alone_is_false() -> None:
    classifier, _ = _make(_FakeProvider("NEW"))
    out = await classifier.is_answer(
        question=_QUESTION, choices=_CHOICES, message="show me my calendar",
    )
    assert out is False


@pytest.mark.asyncio
async def test_both_tokens_genuinely_ambiguous_is_true_fail_safe() -> None:
    """Both tokens present with no clear winner → fail-safe True (treat as answer)."""
    classifier, _ = _make(_FakeProvider("answer or new?"))
    out = await classifier.is_answer(
        question=_QUESTION, choices=_CHOICES, message=_MESSAGE,
    )
    assert out is True


# ------------------------------------------------ MAJOR-2: bounded by a timeout


@pytest.mark.asyncio
async def test_hung_provider_fail_safe_true_quickly() -> None:
    """A hung fast provider fail-safes to True without HOL-blocking the loop.

    The classifier is built with a 50ms timeout against a provider that sleeps 10s;
    the whole call is itself wrapped in a 2s wait_for to PROVE it does not hang.
    """
    classifier, _ = _make(_FakeProvider(hang_seconds=10.0), timeout_s=0.05)
    out = await asyncio.wait_for(
        classifier.is_answer(
            question=_QUESTION, choices=_CHOICES, message=_MESSAGE,
        ),
        timeout=2.0,
    )
    assert out is True


# -------------------------- F-72: explainable low-confidence assumption surface


@pytest.mark.asyncio
async def test_explain_answer_clear_verdict_is_confident() -> None:
    """A clean ANSWER verdict is high-confidence with a clear_verdict reason."""
    classifier, _ = _make(_FakeProvider("ANSWER"))
    v = await classifier.explain_answer(
        question=_QUESTION, choices=_CHOICES, message=_MESSAGE,
    )
    assert isinstance(v, AnswerVerdict)
    assert v.value is True
    assert v.confident is True
    assert v.reason == "clear_verdict"


@pytest.mark.asyncio
async def test_explain_answer_clear_new_is_confident() -> None:
    classifier, _ = _make(_FakeProvider("NEW"))
    v = await classifier.explain_answer(
        question=_QUESTION, choices=_CHOICES, message="show me my calendar",
    )
    assert v.value is False
    assert v.confident is True
    assert v.reason == "clear_verdict"


@pytest.mark.asyncio
async def test_explain_answer_ambiguous_verdict_is_low_confidence() -> None:
    """An ambiguous verdict still fail-safes to answer but is flagged LOW-confidence.

    F-72: the assumption is surfaced (confident=False + a diagnostic reason) instead
    of being silently committed, so a caller can warn the user.
    """
    # Both tokens present but NEITHER leads → the genuine ambiguous fallthrough.
    classifier, _ = _make(_FakeProvider("perhaps answer or new"))
    v = await classifier.explain_answer(
        question=_QUESTION, choices=_CHOICES, message=_MESSAGE,
    )
    assert v.value is True  # unchanged fail-safe direction
    assert v.confident is False
    assert v.reason == "ambiguous_verdict"


@pytest.mark.asyncio
async def test_explain_answer_garbage_verdict_is_low_confidence() -> None:
    classifier, _ = _make(_FakeProvider("maybe"))
    v = await classifier.explain_answer(
        question=_QUESTION, choices=_CHOICES, message=_MESSAGE,
    )
    assert v.value is True
    assert v.confident is False
    assert v.reason == "ambiguous_verdict"


@pytest.mark.asyncio
async def test_explain_answer_empty_message_is_low_confidence_no_call() -> None:
    provider = _FakeProvider("NEW")
    classifier, registry = _make(provider)
    v = await classifier.explain_answer(
        question=_QUESTION, choices=_CHOICES, message="   ",
    )
    assert v.value is True
    assert v.confident is False
    assert v.reason == "empty_message"
    assert registry.tiers_requested == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_explain_answer_no_provider_is_low_confidence() -> None:
    classifier, _ = _make(raise_on_get=RuntimeError("no providers"))
    v = await classifier.explain_answer(
        question=_QUESTION, choices=_CHOICES, message=_MESSAGE,
    )
    assert v.value is True
    assert v.confident is False
    assert v.reason == "no_provider"


@pytest.mark.asyncio
async def test_explain_answer_provider_error_is_low_confidence() -> None:
    classifier, _ = _make(_FakeProvider(raise_on_complete=RuntimeError("boom")))
    v = await classifier.explain_answer(
        question=_QUESTION, choices=_CHOICES, message=_MESSAGE,
    )
    assert v.value is True
    assert v.confident is False
    assert v.reason == "provider_error"


@pytest.mark.asyncio
async def test_explain_answer_timeout_is_low_confidence() -> None:
    classifier, _ = _make(_FakeProvider(hang_seconds=10.0), timeout_s=0.05)
    v = await asyncio.wait_for(
        classifier.explain_answer(
            question=_QUESTION, choices=_CHOICES, message=_MESSAGE,
        ),
        timeout=2.0,
    )
    assert v.value is True
    assert v.confident is False
    assert v.reason == "provider_timeout"


@pytest.mark.asyncio
async def test_is_answer_still_returns_bool_and_matches_explain() -> None:
    """The bool contract of is_answer is preserved (delegates to explain_answer)."""
    for verdict, expected in [("ANSWER", True), ("NEW", False), ("maybe", True)]:
        classifier, _ = _make(_FakeProvider(verdict))
        out = await classifier.is_answer(
            question=_QUESTION, choices=_CHOICES, message="show me my calendar",
        )
        assert out is expected
        assert isinstance(out, bool)


@pytest.mark.asyncio
async def test_cancellation_propagates_through_is_answer() -> None:
    """Cancelling the awaiting task tears it down — CancelledError is not swallowed."""
    classifier, _ = _make(_FakeProvider(hang_seconds=10.0), timeout_s=10.0)
    task = asyncio.ensure_future(
        classifier.is_answer(
            question=_QUESTION, choices=_CHOICES, message=_MESSAGE,
        )
    )
    # Let the task reach the awaited provider.complete().
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --------------------------------------------- resolved model threaded through
#
# _resolve_provider() is SHARED by three callers in this class (is_answer/
# explain_answer, is_steer, is_steer_incoherent). Each has its own provider
# call site, so each gets its own model-capturing test — proving the model
# threading isn't accidentally scoped to only one of the three.


@pytest.mark.asyncio
async def test_is_answer_uses_the_resolved_model_in_the_provider_call() -> None:
    """The (provider, model) pair resolved from get_by_tier_and_model must be
    threaded into provider.complete(..., model=...) for is_answer/explain_answer
    — not hardcoded to ""."""
    provider = _FakeProvider("ANSWER")
    classifier, _ = _make(provider, model="qwen-clarify-answer-v1")
    await classifier.is_answer(question=_QUESTION, choices=_CHOICES, message=_MESSAGE)
    assert provider.models == ["qwen-clarify-answer-v1"]


@pytest.mark.asyncio
async def test_is_steer_uses_the_resolved_model_in_the_provider_call() -> None:
    """Same model-threading guarantee for the is_steer call site."""
    provider = _FakeProvider("STEER")
    classifier, _ = _make(provider, model="qwen-clarify-steer-v1")
    await classifier.is_steer(
        running_ask="prepare me for the interview", message="make it shorter",
    )
    assert provider.models == ["qwen-clarify-steer-v1"]


@pytest.mark.asyncio
async def test_is_steer_incoherent_uses_the_resolved_model_in_the_provider_call() -> None:
    """Same model-threading guarantee for the is_steer_incoherent call site."""
    provider = _FakeProvider("REFINE")
    classifier, _ = _make(provider, model="qwen-clarify-coherence-v1")
    await classifier.is_steer_incoherent(
        running_ask="prepare me for the interview", message="make it shorter",
    )
    assert provider.models == ["qwen-clarify-coherence-v1"]
