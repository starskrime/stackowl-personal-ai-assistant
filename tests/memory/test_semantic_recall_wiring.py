"""Tests for Commit B — embedding registry + LanceDB wired into MemoryAssembly."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

import pytest

from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry

pytestmark = pytest.mark.asyncio


class _StubProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "stub"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:  # noqa: ARG002
        return CompletionResult(
            content="", input_tokens=0, output_tokens=0,
            model="stub", provider_name="stub", duration_ms=0.0,
        )

    async def stream(self, messages: list[Message], model: str, **kwargs: object) -> AsyncIterator[str]:  # noqa: ARG002
        if False:  # pragma: no cover
            yield ""
        return


def _registry() -> ProviderRegistry:
    reg = ProviderRegistry()
    reg.register_mock("stub", _StubProvider(), tier="powerful")
    return reg


# test_fact_extractor_receives_embedding_registry REMOVED with the extractor
# (D08.1). The embedding/LanceDB wiring it rode on is still asserted by the five
# tests above — LanceDB stays, because lessons.lance (221MB) sits behind the
# LessonsIndex and only the fact tables went.


