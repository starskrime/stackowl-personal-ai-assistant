"""MockProvider — in-memory provider for test injection; bypasses TestModeGuard."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

from stackowl.infra.observability import log
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.providers.base import CompletionResult, Message, ModelProvider


class MockProvider(ModelProvider):
    """Returns canned response chunks; never makes network calls.

    Injected into ProviderRegistry via register_mock() in tests.
    Does NOT call TestModeGuard.assert_not_test_mode — it IS the test replacement.
    """

    def __init__(self, name: str = "mock", canned_text: str = "mock response") -> None:
        self._name = name
        self._canned_text = canned_text
        self._call_count = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def stream(self, messages: list[Message], model: str, **kwargs: object) -> AsyncIterator[str]:
        self._call_count += 1
        log.engine.debug(
            "[mock] stream: yielding canned text",
            extra={"_fields": {"provider": self._name, "call_count": self._call_count}},
        )
        words = self._canned_text.split()
        for word in words:
            yield word + " "

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        self._call_count += 1
        log.engine.debug(
            "[mock] complete: returning canned result",
            extra={"_fields": {"provider": self._name, "call_count": self._call_count}},
        )
        return CompletionResult(
            content=self._canned_text,
            input_tokens=len(" ".join(m.content for m in messages).split()),
            output_tokens=len(self._canned_text.split()),
            model="mock-model",
            provider_name=self._name,
            duration_ms=1.0,
        )

    def canned_chunk(self, index: int, is_final: bool = False) -> ResponseChunk:
        """Helper: produce a ResponseChunk for use in pipeline state assertions."""
        return ResponseChunk(
            content=self._canned_text if not is_final else "",
            is_final=is_final,
            chunk_index=index,
            trace_id="test-trace",
            owl_name=self._name,
            duration_ms=1.0,
        )

    @property
    def call_count(self) -> int:
        return self._call_count
