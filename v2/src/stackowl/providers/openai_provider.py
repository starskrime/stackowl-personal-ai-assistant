"""OpenAIProvider — ModelProvider for OpenAI and all OpenAI-compatible endpoints.

Covers: OpenAI, Groq, Together, Mistral, Perplexity, DeepSeek, Ollama, etc.
Adding a new compatible provider requires only a new stackowl.yaml entry — zero new code.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal

import openai

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import ProviderError
from stackowl.infra.observability import log
from stackowl.providers._react import parse_react_action
from stackowl.providers.base import CompletionResult, Message, ModelProvider


def _render_tool_catalog(tool_schemas: list[dict[str, Any]]) -> str:
    """Render tool schemas to a text catalog for text-protocol (no-native-tool-call) mode.

    Delegates to the single renderer on ToolRegistry so the catalog format lives in one
    place. Imported lazily to keep the provider free of an import-time tools dependency.
    """
    from stackowl.tools.registry import ToolRegistry

    return ToolRegistry().render_text_catalog(tool_schemas)


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
        """OpenAI function-calling tool-use loop."""
        import json

        TestModeGuard.assert_not_test_mode("openai.complete_with_tools")
        resolved_iterations = max_iterations if max_iterations != 8 else self._config.tool_max_iterations
        log.engine.debug(
            "[openai] complete_with_tools: entry",
            extra={
                "_fields": {
                    "provider": self._name,
                    "tool_count": len(tool_schemas),
                    "max_iterations": resolved_iterations,
                }
            },
        )
        history_dicts = [{"role": m.role, "content": m.content} for m in (history or [])]
        messages: list[dict[str, Any]] = []
        # Text-protocol fallback: teach the model how to call tools via ACTION:/json
        # text, so weak models without native tool_calls can still act (parsed back
        # by parse_react_action below). Native tool_calls still take priority.
        catalog = _render_tool_catalog(tool_schemas) if tool_schemas else ""
        if catalog:
            system_text = f"{system_text}\n\n{catalog}" if system_text else catalog
        if system_text:
            messages.append({"role": "system", "content": system_text})
        messages.extend(history_dicts)
        messages.append({"role": "user", "content": user_text})
        resolved_model = self._config.default_model
        all_calls: list[dict[str, Any]] = []
        # Phase D — bounded persistence enforcement: at most 2 corrective nudges
        # per turn, so a stubborn give-up cannot explode cost/loops.
        nudge_budget = 2

        async def _enforce(content: str) -> str | None:
            """Run the persistence check for a draft final answer.

            Returns a non-empty directive string to INJECT-and-CONTINUE, or None to
            accept ``content`` as final. Fail-OPEN: if the check is absent, the
            budget is spent, or it raises, returns None (accept the answer).
            """
            nonlocal nudge_budget
            if persistence_check is None or nudge_budget <= 0:
                return None
            try:
                directive = await persistence_check(
                    content, [c["name"] for c in all_calls]
                )
            except Exception as exc:  # fail OPEN — never block/loop on a judge error
                log.engine.error(
                    "[openai] complete_with_tools: persistence_check raised — accepting answer",
                    exc_info=exc,
                    extra={"_fields": {"provider": self._name}},
                )
                return None
            if directive:
                nudge_budget -= 1
                log.engine.info(
                    "[openai] complete_with_tools: persistence nudge — continuing loop",
                    extra={"_fields": {"provider": self._name, "nudge_budget": nudge_budget}},
                )
                return directive
            return None

        for _ in range(resolved_iterations):
            try:
                response = await self._client.chat.completions.create(
                    model=resolved_model,
                    messages=messages,  # type: ignore[arg-type]
                    max_tokens=self._config.max_output_tokens,
                    tools=tool_schemas,  # type: ignore[arg-type]
                )
            except openai.APIError as exc:
                log.engine.error(
                    "[openai] complete_with_tools: API error",
                    exc_info=exc,
                    extra={"_fields": {"provider": self._name}},
                )
                raise ProviderError(self._name, exc) from exc

            choice = response.choices[0]
            if not choice.message.tool_calls:
                content = choice.message.content or ""
                action = parse_react_action(content)
                if action is not None:
                    name, args = action
                    messages.append({"role": "assistant", "content": content})
                    try:
                        result_text = await tool_dispatcher(name, args)
                    except Exception as exc:  # no-hidden-errors: surface to the model, keep looping
                        log.engine.error(
                            "[openai] react dispatch failed",
                            exc_info=exc,
                            extra={"_fields": {"provider": self._name, "tool": name}},
                        )
                        result_text = f"ERROR running {name}: {exc}"
                    all_calls.append({"id": None, "name": name, "args": args, "result": result_text})
                    messages.append({"role": "user", "content": f"OBSERVATION: {result_text}"})
                    continue
                # no action -> draft final answer. Phase D: before accepting it,
                # ask the persistence judge whether the agent delivered or gave up.
                directive = await _enforce(content)
                if directive:
                    messages.append({"role": "user", "content": directive})
                    continue
                log.engine.debug(
                    "[openai] complete_with_tools: exit",
                    extra={"_fields": {"provider": self._name, "calls": len(all_calls)}},
                )
                return content, all_calls

            # Append assistant turn with tool_calls
            messages.append({
                "role": "assistant",
                "content": choice.message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in choice.message.tool_calls
                ],
            })

            # Dispatch and append tool results
            for tc in choice.message.tool_calls:
                fn_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, ValueError) as exc:
                    # no-hidden-errors: a malformed arg blob must not crash the turn —
                    # feed it back as an OBSERVATION so the model can self-correct.
                    log.engine.error(
                        "[openai] complete_with_tools: tool args parse failed",
                        exc_info=exc,
                        extra={"_fields": {"provider": self._name, "tool": fn_name}},
                    )
                    err = f"ERROR: could not parse arguments for {fn_name}: {exc}"
                    all_calls.append({"id": tc.id, "name": fn_name, "args": {}, "result": err})
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": err})
                    continue
                result_text = await tool_dispatcher(fn_name, args)
                all_calls.append({"id": tc.id, "name": fn_name, "args": args, "result": result_text})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})

        log.engine.warning(
            "[openai] complete_with_tools: max_iterations reached",
            extra={"_fields": {"provider": self._name}},
        )
        return "", all_calls

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
