"""ModelProvider ABC — common interface for all AI provider implementations."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Literal

from pydantic import BaseModel, ConfigDict

from stackowl.health.status import HealthStatus


class Message(BaseModel):
    """A single conversation turn."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str


class CompletionResult(BaseModel):
    """The output of a non-streaming provider completion call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str
    input_tokens: int
    output_tokens: int
    model: str
    provider_name: str
    duration_ms: float


class ModelProvider(ABC):
    """Abstract interface for all AI provider backends.

    Concrete classes: AnthropicProvider, OpenAIProvider, GeminiProvider, MockProvider.
    ProviderRegistry holds only ModelProvider references — no concrete class knowledge
    outside the providers package.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Logical name of this provider (from ProviderConfig.name)."""
        ...

    @property
    @abstractmethod
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        """Wire protocol this provider speaks."""
        ...

    @abstractmethod
    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        """Run a non-streaming completion and return the full result."""
        ...

    @abstractmethod
    def stream(self, messages: list[Message], model: str, **kwargs: object) -> AsyncIterator[str]:
        """Yield text deltas as they arrive from the provider."""
        ...

    async def health_check(self) -> HealthStatus:
        """Default lightweight health probe — subclasses may override."""
        t0 = time.monotonic()
        try:
            await self.complete(
                [Message(role="user", content="ping")],
                model="",
                max_tokens=1,
            )
            return HealthStatus(
                name=self.name,
                status="ok",
                message=None,
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return HealthStatus(
                name=self.name,
                status="degraded",
                message=str(exc),
                latency_ms=(time.monotonic() - t0) * 1000,
            )
