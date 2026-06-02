"""GeminiProvider — ModelProvider backed by Google Gemini."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any, Literal

from google import genai
from google.genai import types as genai_types

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import ProviderError
from stackowl.infra.observability import log
from stackowl.providers._blocks import gemini_user_parts, message_has_blocks
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.vision_models import is_vision_model


def _max_tokens(kwargs: dict[str, object], default: int = 4096) -> int:
    val = kwargs.get("max_tokens", default)
    if isinstance(val, int):
        return val
    return int(str(val))


def _build_contents(messages: list[Message]) -> tuple[list[dict[str, Any]], str | None]:
    """Split messages into Gemini contents list and optional system instruction."""
    system_parts = [m.content for m in messages if m.role == "system"]
    system_instruction = "\n\n".join(system_parts) if system_parts else None
    contents: list[dict[str, Any]] = []
    role_map = {"user": "user", "assistant": "model", "tool": "user"}
    for m in messages:
        if m.role == "system":
            continue
        # A message with image/document blocks → multimodal inline_data parts;
        # a plain message keeps the single text part (B6 minimal change).
        parts = gemini_user_parts(m) if message_has_blocks(m) else [{"text": m.content}]
        contents.append({"role": role_map.get(m.role, "user"), "parts": parts})
    return contents, system_instruction


class GeminiProvider(ModelProvider):
    """Google Gemini provider using google-genai SDK."""

    def __init__(self, config: ProviderConfig, api_key: str) -> None:
        self._name = config.name
        self._config = config
        self._client = genai.Client(api_key=api_key)
        log.engine.debug(
            "[gemini] init: provider constructed",
            extra={"_fields": {"name": self._name, "model": config.default_model}},
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "gemini"

    @property
    def supports_vision(self) -> bool:
        """True when the configured Gemini model is multimodal (1.5+/2.x)."""
        return is_vision_model(self._config.default_model)

    @property
    def supports_document(self) -> bool:
        """A multimodal Gemini model also accepts inline PDF document blocks (Mode B)."""
        return is_vision_model(self._config.default_model)

    async def stream(self, messages: list[Message], model: str, **kwargs: object) -> AsyncIterator[str]:
        TestModeGuard.assert_not_test_mode("gemini.stream")
        log.engine.debug(
            "[gemini] stream: entry",
            extra={"_fields": {"provider": self._name, "model": model, "msg_count": len(messages)}},
        )
        contents, system_instruction = _build_contents(messages)
        resolved_model = model or self._config.default_model
        config = genai_types.GenerateContentConfig(
            system_instruction=system_instruction,
            max_output_tokens=_max_tokens(kwargs),
        )
        try:
            async for chunk in await self._client.aio.models.generate_content_stream(
                model=resolved_model,
                contents=contents,
                config=config,
            ):
                if chunk.text:
                    yield chunk.text
        except Exception as exc:
            log.engine.error(
                "[gemini] stream: error",
                exc_info=exc,
                extra={"_fields": {"provider": self._name}},
            )
            raise ProviderError(self._name, exc) from exc
        log.engine.debug("[gemini] stream: exit", extra={"_fields": {"provider": self._name}})

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        TestModeGuard.assert_not_test_mode("gemini.complete")
        log.engine.debug(
            "[gemini] complete: entry",
            extra={"_fields": {"provider": self._name, "model": model}},
        )
        t0 = time.monotonic()
        contents, system_instruction = _build_contents(messages)
        resolved_model = model or self._config.default_model
        config = genai_types.GenerateContentConfig(
            system_instruction=system_instruction,
            max_output_tokens=_max_tokens(kwargs),
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=resolved_model,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            log.engine.error(
                "[gemini] complete: error",
                exc_info=exc,
                extra={"_fields": {"provider": self._name}},
            )
            raise ProviderError(self._name, exc) from exc
        duration_ms = (time.monotonic() - t0) * 1000
        text = response.text or ""
        usage = response.usage_metadata
        result = CompletionResult(
            content=text,
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(usage.candidates_token_count or 0) if usage else 0,
            model=resolved_model,
            provider_name=self._name,
            duration_ms=duration_ms,
        )
        # E8-S0cost — single recording site: every provider call records its spend.
        await self._record_cost(
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            duration_ms=duration_ms,
        )
        log.engine.debug(
            "[gemini] complete: exit",
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
