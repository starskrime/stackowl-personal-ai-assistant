"""OpenAIProvider — ModelProvider for OpenAI and all OpenAI-compatible endpoints.

Covers: OpenAI, Groq, Together, Mistral, Perplexity, DeepSeek, Ollama, etc.
Adding a new compatible provider requires only a new stackowl.yaml entry — zero new code.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Literal

import openai

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import ProviderError
from stackowl.infra.observability import log
from stackowl.providers.base import CompletionResult, Message, ModelProvider


def _max_tokens(kwargs: dict[str, object], default: int = 4096) -> int:
    val = kwargs.get("max_tokens", default)
    if isinstance(val, int):
        return val
    return int(str(val))


class OpenAIProvider(ModelProvider):
    """OpenAI-compatible provider — one class handles all OpenAI-protocol endpoints."""

    def __init__(self, config: ProviderConfig, api_key: str) -> None:
        self._name = config.name
        self._config = config
        self._client = openai.AsyncOpenAI(
            base_url=config.base_url or None,
            api_key=api_key or "no-key-needed",
        )
        log.engine.debug(
            "[openai] init: provider constructed",
            extra={"_fields": {"name": self._name, "model": config.default_model}},
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def stream(self, messages: list[Message], model: str, **kwargs: object) -> AsyncIterator[str]:
        TestModeGuard.assert_not_test_mode("openai.stream")
        log.engine.debug(
            "[openai] stream: entry",
            extra={"_fields": {"provider": self._name, "model": model, "msg_count": len(messages)}},
        )
        resolved_model = model or self._config.default_model
        oai_msgs = [{"role": m.role, "content": m.content} for m in messages]
        try:
            stream_resp = await self._client.chat.completions.create(
                model=resolved_model,
                messages=oai_msgs,  # type: ignore[arg-type]
                max_tokens=_max_tokens(kwargs),
                stream=True,
            )
            async for chunk in stream_resp:  # type: ignore[union-attr]
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta
        except openai.APIError as exc:
            log.engine.error(
                "[openai] stream: API error",
                exc_info=exc,
                extra={"_fields": {"provider": self._name}},
            )
            raise ProviderError(self._name, exc) from exc
        log.engine.debug("[openai] stream: exit", extra={"_fields": {"provider": self._name}})

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        TestModeGuard.assert_not_test_mode("openai.complete")
        log.engine.debug(
            "[openai] complete: entry",
            extra={"_fields": {"provider": self._name, "model": model}},
        )
        t0 = time.monotonic()
        resolved_model = model or self._config.default_model
        oai_msgs = [{"role": m.role, "content": m.content} for m in messages]
        try:
            response = await self._client.chat.completions.create(
                model=resolved_model,
                messages=oai_msgs,  # type: ignore[arg-type]
                max_tokens=_max_tokens(kwargs),
            )
        except openai.APIError as exc:
            log.engine.error(
                "[openai] complete: API error",
                exc_info=exc,
                extra={"_fields": {"provider": self._name}},
            )
            raise ProviderError(self._name, exc) from exc
        duration_ms = (time.monotonic() - t0) * 1000
        choice = response.choices[0]
        usage = response.usage
        result = CompletionResult(
            content=choice.message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            model=response.model,
            provider_name=self._name,
            duration_ms=duration_ms,
        )
        log.engine.debug(
            "[openai] complete: exit",
            extra={
                "_fields": {
                    "provider": self._name,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "duration_ms": duration_ms,
                }
            },
        )
        return result
