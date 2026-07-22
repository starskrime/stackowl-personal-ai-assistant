"""Tests for the shared classifier/judge primitives (Pieces A, B, C).

These are the safety net every later classifier-migration phase's "no
behavior change" claim gets verified against — see
``~/.claude/plans/woolly-dreaming-wilkes.md``, Workstream A.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Literal

import pytest

from stackowl.interaction.classifier_base import (
    parse_two_token_verdict,
    resolve_cascade_tier,
    resolve_fixed_tier,
    safe_complete,
)
from stackowl.providers.base import CompletionResult, Message, ModelProvider

_LOGGER = logging.getLogger("test_classifier_base")


class _FakeProvider(ModelProvider):
    """Minimal ModelProvider stand-in honoring the real abstract signature."""

    def __init__(
        self,
        *,
        content: str = "ok",
        raise_on_complete: Exception | None = None,
        hang_seconds: float | None = None,
    ) -> None:
        self._content = content
        self._raise = raise_on_complete
        self._hang_seconds = hang_seconds
        self.calls: list[tuple[list[Message], str, dict[str, object]]] = []

    @property
    def name(self) -> str:
        return "fake-provider"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object,
    ) -> CompletionResult:
        self.calls.append((messages, model, kwargs))
        if self._hang_seconds is not None:
            await asyncio.sleep(self._hang_seconds)
        if self._raise is not None:
            raise self._raise
        return CompletionResult(
            content=self._content, input_tokens=1, output_tokens=1,
            model=model, provider_name=self.name, duration_ms=1.0,
        )

    async def stream(  # pragma: no cover — unused by these tests
        self, messages: list[Message], model: str, **kwargs: object,
    ) -> AsyncIterator[str]:
        yield ""


class _FakeRegistry:
    """get_by_tier / get_with_cascade stand-in, each independently scriptable."""

    def __init__(
        self,
        *,
        fixed_result: tuple[ModelProvider, str] | None = None,
        fixed_raises: Exception | None = None,
        cascade_result: tuple[ModelProvider, str] | None = None,
        cascade_raises: Exception | None = None,
    ) -> None:
        self._fixed_result = fixed_result
        self._fixed_raises = fixed_raises
        self._cascade_result = cascade_result
        self._cascade_raises = cascade_raises

    def get_by_tier(self, tier: str) -> tuple[ModelProvider, str]:
        if self._fixed_raises is not None:
            raise self._fixed_raises
        assert self._fixed_result is not None
        return self._fixed_result

    def get_with_cascade(self, tier: str) -> tuple[ModelProvider, str]:
        if self._cascade_raises is not None:
            raise self._cascade_raises
        assert self._cascade_result is not None
        return self._cascade_result


# ------------------------------------------------------------------ Piece A


def test_resolve_fixed_tier_success() -> None:
    provider = _FakeProvider()
    registry = _FakeRegistry(fixed_result=(provider, "model-x"))
    result = resolve_fixed_tier(registry, "fast", logger=_LOGGER, call_name="t")  # type: ignore[arg-type]
    assert result == (provider, "model-x")


def test_resolve_fixed_tier_failure_returns_none() -> None:
    registry = _FakeRegistry(fixed_raises=RuntimeError("no providers"))
    result = resolve_fixed_tier(registry, "fast", logger=_LOGGER, call_name="t")  # type: ignore[arg-type]
    assert result is None


def test_resolve_cascade_tier_success() -> None:
    provider = _FakeProvider()
    registry = _FakeRegistry(cascade_result=(provider, "model-y"))
    result = resolve_cascade_tier(registry, "fast", logger=_LOGGER, call_name="t")  # type: ignore[arg-type]
    assert result == (provider, "model-y")


def test_resolve_cascade_tier_failure_returns_none() -> None:
    registry = _FakeRegistry(cascade_raises=RuntimeError("all open"))
    result = resolve_cascade_tier(registry, "fast", logger=_LOGGER, call_name="t")  # type: ignore[arg-type]
    assert result is None


# ------------------------------------------------------------------ Piece B


@pytest.mark.asyncio
async def test_safe_complete_success_passes_disable_thinking_and_max_tokens() -> None:
    provider = _FakeProvider(content="COMMIT")
    outcome = await safe_complete(
        provider, "model-x", [Message(role="user", content="hi")],
        max_tokens=4, timeout_s=10.0, logger=_LOGGER, call_name="t",
    )
    assert outcome.result is not None
    assert outcome.result.content == "COMMIT"
    assert outcome.timed_out is False
    _, _, kwargs = provider.calls[0]
    assert kwargs["max_tokens"] == 4
    assert kwargs["disable_thinking"] is True
    assert "response_format" not in kwargs


@pytest.mark.asyncio
async def test_safe_complete_forwards_response_format_when_given() -> None:
    provider = _FakeProvider(content='{"verdict": "NONE"}')
    schema = {"type": "json_schema", "json_schema": {"name": "verdict"}}
    outcome = await safe_complete(
        provider, "model-x", [Message(role="user", content="hi")],
        max_tokens=20, timeout_s=10.0, logger=_LOGGER, call_name="t",
        response_format=schema,
    )
    assert outcome.result is not None
    _, _, kwargs = provider.calls[0]
    assert kwargs["response_format"] == schema


@pytest.mark.asyncio
async def test_safe_complete_forwards_temperature_when_given() -> None:
    """feedback_classifier.py/retry_intent_classifier.py pin temperature=0.0
    for deterministic JSON parsing — must actually reach the provider call,
    not be silently dropped (openai_provider.py never forwarded a bare
    temperature kwarg until this migration surfaced the gap)."""
    provider = _FakeProvider(content='{"is_retry": true}')
    outcome = await safe_complete(
        provider, "model-x", [Message(role="user", content="hi")],
        max_tokens=128, timeout_s=10.0, logger=_LOGGER, call_name="t",
        temperature=0.0,
    )
    assert outcome.result is not None
    _, _, kwargs = provider.calls[0]
    assert kwargs["temperature"] == 0.0


@pytest.mark.asyncio
async def test_safe_complete_omits_temperature_when_not_given() -> None:
    provider = _FakeProvider()
    await safe_complete(
        provider, "model-x", [Message(role="user", content="hi")],
        max_tokens=4, timeout_s=10.0, logger=_LOGGER, call_name="t",
    )
    _, _, kwargs = provider.calls[0]
    assert "temperature" not in kwargs


@pytest.mark.asyncio
async def test_safe_complete_disable_thinking_overridable() -> None:
    provider = _FakeProvider()
    await safe_complete(
        provider, "model-x", [Message(role="user", content="hi")],
        max_tokens=4, timeout_s=10.0, logger=_LOGGER, call_name="t",
        disable_thinking=False,
    )
    _, _, kwargs = provider.calls[0]
    assert kwargs["disable_thinking"] is False


@pytest.mark.asyncio
async def test_safe_complete_no_timeout_mode() -> None:
    """timeout_s=None must NOT wrap in asyncio.wait_for — preserves call sites
    (router.py, acceptance_llm.py, critic_scorer_handler.py, delivery_gate.py's
    apology generator) that currently have no timeout at all."""
    provider = _FakeProvider(hang_seconds=0.05)
    outcome = await safe_complete(
        provider, "model-x", [Message(role="user", content="hi")],
        max_tokens=4, timeout_s=None, logger=_LOGGER, call_name="t",
    )
    assert outcome.result is not None  # completed fine — no timeout was ever armed


@pytest.mark.asyncio
async def test_safe_complete_timeout_returns_none_and_flags_timed_out() -> None:
    """timed_out=True distinguishes this from a generic provider error — needed
    by callers like intent_classifier.py's is_answer, whose AnswerVerdict.reason
    tags "provider_timeout" separately from "provider_error" for F-72 auditing."""
    provider = _FakeProvider(hang_seconds=5.0)
    outcome = await safe_complete(
        provider, "model-x", [Message(role="user", content="hi")],
        max_tokens=4, timeout_s=0.05, logger=_LOGGER, call_name="t",
    )
    assert outcome.result is None
    assert outcome.timed_out is True


@pytest.mark.asyncio
async def test_safe_complete_provider_error_returns_none_not_timed_out() -> None:
    provider = _FakeProvider(raise_on_complete=RuntimeError("boom"))
    outcome = await safe_complete(
        provider, "model-x", [Message(role="user", content="hi")],
        max_tokens=4, timeout_s=10.0, logger=_LOGGER, call_name="t",
    )
    assert outcome.result is None
    assert outcome.timed_out is False


@pytest.mark.asyncio
async def test_safe_complete_never_swallows_cancelled_error() -> None:
    """CancelledError is not an Exception subclass — must propagate, never be
    treated as a fail-safe-to-None case (that would defeat real task cancellation,
    e.g. a turn-level deadline or a user hitting stop)."""

    class _CancellingProvider(ModelProvider):
        @property
        def name(self) -> str:
            return "cancelling"

        @property
        def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
            return "openai"

        async def complete(self, messages, model, **kwargs):  # noqa: ANN001, ANN003, ANN201
            raise asyncio.CancelledError

        async def stream(self, messages, model, **kwargs):  # noqa: ANN001, ANN003, ANN201
            yield ""

    with pytest.raises(asyncio.CancelledError):
        await safe_complete(
            _CancellingProvider(), "model-x", [Message(role="user", content="hi")],
            max_tokens=4, timeout_s=10.0, logger=_LOGGER, call_name="t",
        )


# ------------------------------------------------------------------ Piece C


def test_parse_two_token_verdict_true_only() -> None:
    value, confident = parse_two_token_verdict(
        "COMMIT", true_token="commit", false_token="none",
        ambiguous_default=False, use_leading_token_tiebreak=False,
    )
    assert (value, confident) == (True, True)


def test_parse_two_token_verdict_false_only() -> None:
    value, confident = parse_two_token_verdict(
        " none\n", true_token="commit", false_token="none",
        ambiguous_default=False, use_leading_token_tiebreak=False,
    )
    assert (value, confident) == (False, True)


def test_parse_two_token_verdict_neither_present_falls_to_ambiguous_default() -> None:
    value, confident = parse_two_token_verdict(
        "maybe", true_token="commit", false_token="none",
        ambiguous_default=False, use_leading_token_tiebreak=False,
    )
    assert (value, confident) == (False, False)
    value2, confident2 = parse_two_token_verdict(
        "maybe", true_token="answer", false_token="new",
        ambiguous_default=True, use_leading_token_tiebreak=True,
    )
    assert (value2, confident2) == (True, False)


def test_parse_two_token_verdict_both_present_no_tiebreak_always_ambiguous() -> None:
    """Matches intent_classifier.py's _parse_steer_verdict (is_steer): the
    EXPENSIVE direction is deliberately never granted on a both-present tie,
    even when one token clearly leads — asymmetric from the leading-token case."""
    value, confident = parse_two_token_verdict(
        "steer or new?", true_token="steer", false_token="new",
        ambiguous_default=False, use_leading_token_tiebreak=False,
    )
    assert (value, confident) == (False, False)
    # Even when the true_token leads, no-tiebreak mode still ignores it.
    value2, confident2 = parse_two_token_verdict(
        "steer, not new", true_token="steer", false_token="new",
        ambiguous_default=False, use_leading_token_tiebreak=False,
    )
    assert (value2, confident2) == (False, False)


def test_parse_two_token_verdict_both_present_leading_tiebreak_true_wins() -> None:
    """Matches intent_classifier.py's _parse_verdict (is_answer) and
    _parse_coherence_verdict (is_steer_incoherent): a both-present tie is
    broken by whichever token the verdict STARTS WITH."""
    value, confident = parse_two_token_verdict(
        "answer — this does not need clarification, new info given",
        true_token="answer", false_token="new",
        ambiguous_default=True, use_leading_token_tiebreak=True,
    )
    assert (value, confident) == (True, True)


def test_parse_two_token_verdict_both_present_leading_tiebreak_false_wins() -> None:
    value, confident = parse_two_token_verdict(
        "new — this is not an answer",
        true_token="answer", false_token="new",
        ambiguous_default=True, use_leading_token_tiebreak=True,
    )
    assert (value, confident) == (False, True)


def test_parse_two_token_verdict_both_present_leading_tiebreak_no_clear_lead() -> None:
    """Neither token leads (verdict starts with neither exact token) -> the
    leading-token tiebreak can't resolve it -> ambiguous fallback."""
    value, confident = parse_two_token_verdict(
        "hmm, answer or new, unclear",
        true_token="answer", false_token="new",
        ambiguous_default=True, use_leading_token_tiebreak=True,
    )
    assert (value, confident) == (True, False)


def test_parse_two_token_verdict_case_insensitive() -> None:
    value, confident = parse_two_token_verdict(
        "  COMMIT.", true_token="commit", false_token="none",
        ambiguous_default=False, use_leading_token_tiebreak=False,
    )
    assert (value, confident) == (True, True)
