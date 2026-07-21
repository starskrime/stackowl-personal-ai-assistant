"""Task 6 — FactExtractor trust taint: any tool-role message in the batch marks
all extracted facts untrusted; a pure user/assistant batch keeps the default
'self' trust from trust_for_source('conversation_fact').
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

import pytest

from stackowl.memory.fact_extractor import FactExtractor
from stackowl.memory.trust import trust_for_source, SAFE_DEFAULT
from stackowl.providers.base import CompletionResult, Message, ModelProvider


# ---------------------------------------------------------------------------
# Test-mode bypass (mirrors test_story_6_3 pattern)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *a, **kw: None,
    )


# ---------------------------------------------------------------------------
# Minimal stub provider — returns one draft fact
# ---------------------------------------------------------------------------


class _StubProvider(ModelProvider):
    """Returns a single-fact JSON list so extract() always produces >= 1 StagedFact."""

    @property
    def name(self) -> str:
        return "stub"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        return CompletionResult(
            content='[{"content": "The sky is blue", "confidence": 0.9}]',
            input_tokens=5,
            output_tokens=3,
            model="stub",
            provider_name="stub",
            duration_ms=0.5,
        )

    def stream(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class _EmptyProvider(_StubProvider):
    """Returns EMPTY content — a reasoning model that spent its whole output
    budget inside a <think> block (truncated at the cap)."""

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        return CompletionResult(
            content="",
            input_tokens=5,
            output_tokens=0,
            model="stub",
            provider_name="stub",
            duration_ms=0.5,
        )


@pytest.mark.asyncio
async def test_empty_model_output_returns_no_facts_not_crash() -> None:
    """2026-06-23 break: empty model output must yield [] (honest zero), NOT raise
    FactExtractionParseError and abort the mining cycle."""
    extractor = FactExtractor(provider=_EmptyProvider())
    convo = [
        Message(role="user", content="I love hiking."),
        Message(role="assistant", content="That's great!"),
    ]
    facts = await extractor.extract(convo, session_id="sess-empty")
    assert facts == [], f"empty output must extract zero facts, got {facts!r}"


@pytest.mark.asyncio
async def test_extracted_facts_untrusted_when_batch_has_tool_role() -> None:
    """Any tool-role message in the batch taints all extracted facts as untrusted."""
    extractor = FactExtractor(provider=_StubProvider())
    convo = [
        Message(role="user", content="What did the API return?"),
        Message(role="tool", content="external tool output: 42"),
    ]
    facts = await extractor.extract(convo, session_id="sess-tool")
    assert facts, "extractor must return at least one fact"
    assert all(f.trust == "untrusted" for f in facts), (
        f"expected all facts untrusted, got: {[f.trust for f in facts]}"
    )


@pytest.mark.asyncio
async def test_extracted_facts_self_when_no_tool_role() -> None:
    """A pure user/assistant batch keeps the 'self' trust tier."""
    extractor = FactExtractor(provider=_StubProvider())
    convo = [
        Message(role="user", content="I love hiking."),
        Message(role="assistant", content="That's great!"),
    ]
    facts = await extractor.extract(convo, session_id="sess-clean")
    assert facts, "extractor must return at least one fact"
    expected_trust = trust_for_source("conversation_fact")  # == "self"
    assert all(f.trust == expected_trust for f in facts), (
        f"expected all facts {expected_trust!r}, got: {[f.trust for f in facts]}"
    )


# ---------------------------------------------------------------------------
# Task 16 — FactExtractor threads the resolved model through provider.complete()
# instead of hardcoding model="".
# ---------------------------------------------------------------------------


class _ModelCapturingProvider(ModelProvider):
    """Records the ``model`` kwarg each ``complete()`` call receives — proves
    :class:`FactExtractor` forwards ``self._model`` rather than hardcoding
    ``model=""``. Returns a fixed, valid single-fact draft list."""

    def __init__(self) -> None:
        self.seen_models: list[str] = []

    @property
    def name(self) -> str:
        return "model-capturing-fact"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        self.seen_models.append(model)
        return CompletionResult(
            content='[{"content": "The sky is blue", "confidence": 0.9}]',
            input_tokens=5,
            output_tokens=3,
            model="model-capturing-fact",
            provider_name="model-capturing-fact",
            duration_ms=0.5,
        )

    def stream(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_extract_threads_constructor_model_to_provider_complete() -> None:
    """FactExtractor(model=...) must forward that exact model string into
    provider.complete(), not the hardcoded model="" default.

    Genuinely discriminating: if the constructor kept ignoring ``model=`` and
    the internal ``complete()`` call kept hardcoding ``model=""``,
    ``seen_models`` would be ``[""]`` instead of the sentinel value below.
    """
    provider = _ModelCapturingProvider()
    extractor = FactExtractor(provider=provider, model="fact-extractor-resolved-model")
    convo = [
        Message(role="user", content="I love hiking."),
        Message(role="assistant", content="That's great!"),
    ]
    facts = await extractor.extract(convo, session_id="sess-model")
    assert facts, "extractor must return at least one fact"
    assert provider.seen_models == ["fact-extractor-resolved-model"], (
        f"expected provider.complete to receive the constructor model, got: {provider.seen_models!r}"
    )


@pytest.mark.asyncio
async def test_extract_default_model_is_empty_string_when_unset() -> None:
    """Byte-identical default: FactExtractor(model=<unset>) still passes model="" —
    proves the new parameter is additive, not a behaviour change for existing callers."""
    provider = _ModelCapturingProvider()
    extractor = FactExtractor(provider=provider)
    convo = [
        Message(role="user", content="I love hiking."),
        Message(role="assistant", content="That's great!"),
    ]
    await extractor.extract(convo, session_id="sess-default-model")
    assert provider.seen_models == [""], (
        f"expected default model='', got: {provider.seen_models!r}"
    )
