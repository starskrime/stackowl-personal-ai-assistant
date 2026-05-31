"""AnthropicProvider — ModelProvider backed by the Anthropic Messages API."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal

import anthropic

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import ProviderError
from stackowl.infra.observability import log
from stackowl.pipeline.persistence import summarize_tool_outcomes
from stackowl.providers._truncate import (
    CONTEXT_CHAR_BUDGET,
    trim_messages_to_budget,
    truncate_observation,
)
from stackowl.providers._wrapup import WRAPUP_DIRECTIVE
from stackowl.providers.base import CompletionResult, Message, ModelProvider


def _last_assistant_text(messages: list[dict[str, Any]]) -> str:
    """Last non-empty assistant text already in ``messages`` (newest first), or "".

    Anthropic assistant turns carry a list of content blocks; concatenate any
    ``text`` blocks. A plain-string assistant content is also handled.
    """
    for m in reversed(messages):
        if m.get("role") != "assistant":
            continue
        content = m.get("content")
        if isinstance(content, str):
            if content.strip():
                return content
            continue
        if isinstance(content, list):
            text = "".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
            if text.strip():
                return text
    return ""


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

    async def complete_with_tools(
        self,
        user_text: str,
        system_text: str | None,
        tool_schemas: list[dict[str, Any]],
        tool_dispatcher: Callable[[str, dict[str, Any]], Awaitable[str]],
        max_iterations: int = 8,
        history: list[Message] | None = None,
        persistence_check: Callable[[str, list[str]], Awaitable[str | None]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Anthropic native tool-use loop using content blocks."""
        TestModeGuard.assert_not_test_mode("anthropic.complete_with_tools")
        resolved_iterations = max_iterations if max_iterations != 8 else self._config.tool_max_iterations
        log.engine.debug(
            "[anthropic] complete_with_tools: entry",
            extra={"_fields": {
                "provider": self._name,
                "tool_count": len(tool_schemas),
                "max_iterations": resolved_iterations,
            }},
        )
        history_dicts = [{"role": m.role, "content": m.content} for m in (history or [])]
        messages: list[dict[str, Any]] = [
            *history_dicts,
            {"role": "user", "content": user_text},
        ]
        system_kwargs: dict[str, Any] = {"system": system_text} if system_text else {}
        all_calls: list[dict[str, Any]] = []
        # Phase D — bounded persistence enforcement: at most 2 corrective nudges
        # per turn, so a stubborn give-up cannot explode cost/loops.
        nudge_budget = 2

        async def _enforce(content: str) -> str | None:
            """Run the persistence check for a draft final answer (fail-OPEN)."""
            nonlocal nudge_budget
            if persistence_check is None or nudge_budget <= 0:
                return None
            try:
                directive = await persistence_check(
                    content, summarize_tool_outcomes(all_calls)
                )
            except Exception as exc:  # fail OPEN — never block/loop on a judge error
                log.engine.error(
                    "[anthropic] complete_with_tools: persistence_check raised — accepting answer",
                    exc_info=exc,
                    extra={"_fields": {"provider": self._name}},
                )
                return None
            if directive:
                nudge_budget -= 1
                log.engine.info(
                    "[anthropic] complete_with_tools: persistence nudge — continuing loop",
                    extra={"_fields": {"provider": self._name, "nudge_budget": nudge_budget}},
                )
                return directive
            return None

        budget = (
            int(self._config.context_chars * 0.8)
            if self._config.context_chars
            else CONTEXT_CHAR_BUDGET
        )

        for _ in range(resolved_iterations):
            # Bound total context BEFORE the call. Only tool_result CONTENT is
            # elided (never the message itself), so tool_use/tool_result pairing
            # stays valid for the Anthropic API.
            messages = trim_messages_to_budget(messages, budget)
            try:
                response = await self._client.messages.create(
                    model=self._config.default_model,
                    messages=messages,  # type: ignore[arg-type]
                    max_tokens=self._config.max_output_tokens,
                    tools=tool_schemas,  # type: ignore[arg-type]
                    **system_kwargs,
                )
            except anthropic.APIError as exc:
                log.engine.error(
                    "[anthropic] complete_with_tools: API error",
                    exc_info=exc,
                    extra={"_fields": {"provider": self._name}},
                )
                raise ProviderError(self._name, exc) from exc

            if response.stop_reason != "tool_use":
                text = "".join(b.text for b in response.content if hasattr(b, "text"))
                # Phase D: before accepting the draft, ask the persistence judge
                # whether the agent delivered or gave up.
                directive = await _enforce(text)
                if directive:
                    messages.append({"role": "user", "content": directive})
                    continue
                log.engine.debug(
                    "[anthropic] complete_with_tools: exit",
                    extra={"_fields": {"provider": self._name, "calls": len(all_calls)}},
                )
                return text, all_calls

            # Build assistant turn with all content blocks
            assistant_content: list[dict[str, Any]] = []
            for b in response.content:
                if b.type == "text":
                    assistant_content.append({"type": "text", "text": b.text})
                elif b.type == "tool_use":
                    assistant_content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
            messages.append({"role": "assistant", "content": assistant_content})

            # Dispatch each tool call and append results as a user turn
            tool_results: list[dict[str, Any]] = []
            for b in response.content:
                if b.type != "tool_use":
                    continue
                result_text = await tool_dispatcher(b.name, dict(b.input))
                # Cap the observation so a huge tool result can't overflow the context.
                capped = truncate_observation(result_text)
                all_calls.append({"id": b.id, "name": b.name, "args": b.input, "result": capped})
                tool_results.append({"type": "tool_result", "tool_use_id": b.id, "content": capped})
            messages.append({"role": "user", "content": tool_results})

        log.engine.warning(
            "[anthropic] complete_with_tools: max_iterations reached",
            extra={"_fields": {"provider": self._name}},
        )
        # Phase F — graceful max-out: never return empty. Make ONE final model call
        # WITHOUT tools (keeping the system prompt) after a global, language-agnostic
        # wrap-up directive so the user always gets a coherent answer. Fail-open: any
        # provider error falls back to the last assistant text already gathered.
        try:
            messages = trim_messages_to_budget(messages, budget)
            messages.append({"role": "user", "content": WRAPUP_DIRECTIVE})
            wrapup = await self._client.messages.create(
                model=self._config.default_model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=self._config.max_output_tokens,
                **system_kwargs,
            )
            text = "".join(b.text for b in wrapup.content if hasattr(b, "text"))
            if text.strip():
                log.engine.debug(
                    "[anthropic] complete_with_tools: wrap-up answer delivered at max-out",
                    extra={"_fields": {"provider": self._name, "len": len(text)}},
                )
                return text, all_calls
        except Exception as exc:  # fail-open — never surface silence on a wrap-up error
            log.engine.error(
                "[anthropic] complete_with_tools: wrap-up call failed — falling back",
                exc_info=exc,
                extra={"_fields": {"provider": self._name}},
            )
        fallback = _last_assistant_text(messages)
        if fallback.strip():
            return fallback, all_calls
        return "", all_calls

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
