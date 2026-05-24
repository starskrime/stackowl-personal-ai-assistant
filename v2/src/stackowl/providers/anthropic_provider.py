"""AnthropicProvider — ModelProvider backed by the Anthropic Messages API."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Literal

import anthropic

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


class AnthropicProvider(ModelProvider):
    """Anthropic Messages API provider (claude-* family)."""

    def __init__(self, config: ProviderConfig, api_key: str) -> None:
        self._name = config.name
        self._config = config
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        log.engine.debug(
            "[anthropic] init: provider constructed",
            extra={"_fields": {"name": self._name, "model": config.default_model}},
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "anthropic"

    async def stream(self, messages: list[Message], model: str, **kwargs: object) -> AsyncIterator[str]:
        TestModeGuard.assert_not_test_mode("anthropic.stream")
        log.engine.debug(
            "[anthropic] stream: entry",
            extra={"_fields": {"provider": self._name, "model": model, "msg_count": len(messages)}},
        )
        system_parts = [m.content for m in messages if m.role == "system"]
        chat_msgs = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
        resolved_model = model or self._config.default_model
        stream_kwargs: dict[str, object] = {"system": "\n\n".join(system_parts)} if system_parts else {}
        try:
            async with self._client.messages.stream(
                model=resolved_model,
                messages=chat_msgs,  # type: ignore[arg-type]
                max_tokens=_max_tokens(kwargs),
                **stream_kwargs,  # type: ignore[arg-type]
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except anthropic.APIError as exc:
            log.engine.error(
                "[anthropic] stream: API error",
                exc_info=exc,
                extra={"_fields": {"provider": self._name}},
            )
            raise ProviderError(self._name, exc) from exc
        log.engine.debug("[anthropic] stream: exit", extra={"_fields": {"provider": self._name}})

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        TestModeGuard.assert_not_test_mode("anthropic.complete")
        log.engine.debug(
            "[anthropic] complete: entry",
            extra={"_fields": {"provider": self._name, "model": model}},
        )
        t0 = time.monotonic()
        system_parts = [m.content for m in messages if m.role == "system"]
        chat_msgs = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
        resolved_model = model or self._config.default_model
        try:
            if system_parts:
                response = await self._client.messages.create(
                    model=resolved_model,
                    messages=chat_msgs,  # type: ignore[arg-type]
                    max_tokens=_max_tokens(kwargs),
                    system="\n\n".join(system_parts),
                )
            else:
                response = await self._client.messages.create(
                    model=resolved_model,
                    messages=chat_msgs,  # type: ignore[arg-type]
                    max_tokens=_max_tokens(kwargs),
                )
        except anthropic.APIError as exc:
            log.engine.error(
                "[anthropic] complete: API error",
                exc_info=exc,
                extra={"_fields": {"provider": self._name}},
            )
            raise ProviderError(self._name, exc) from exc
        duration_ms = (time.monotonic() - t0) * 1000
        content = "".join(b.text for b in response.content if hasattr(b, "text"))
        result = CompletionResult(
            content=content,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
            provider_name=self._name,
            duration_ms=duration_ms,
        )
        log.engine.debug(
            "[anthropic] complete: exit",
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
