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
from stackowl.providers._resilient_round import _is_transport_error
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.model_config import resolve_model_override
from stackowl.providers.vision_models import is_vision_model

# Gemini candidate finish_reason names that are a NORMAL (non-blocked) completion.
# Anything else with empty text (SAFETY / RECITATION / BLOCKLIST / PROHIBITED_CONTENT
# / SPII …) is a content BLOCK, not a transient empty generation. These are SDK
# protocol enum names (vendor wire detail confined to this thin adapter), NOT
# natural-language keywords.
_NORMAL_FINISH_REASONS = frozenset({"STOP", "MAX_TOKENS", "FINISH_REASON_UNSPECIFIED"})


def _max_tokens(kwargs: dict[str, object], default: int = 4096) -> int:
    val = kwargs.get("max_tokens", default)
    if isinstance(val, int):
        return val
    return int(str(val))


def _block_reason(response: Any) -> str | None:
    """Return a non-empty reason string when a generation was blocked/filtered.

    Reads the prompt-side ``prompt_feedback.block_reason`` and the response-side
    candidate ``finish_reason``. Returns ``None`` for a normal completion (STOP /
    MAX_TOKENS / unspecified) so an ordinary empty generation is never misreported
    as a block. Fail-open: any odd/missing shape returns ``None``.
    """
    try:
        feedback = getattr(response, "prompt_feedback", None)
        block = getattr(feedback, "block_reason", None) if feedback is not None else None
        if block:
            return str(getattr(block, "name", block))
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            fr = getattr(candidates[0], "finish_reason", None)
            if fr is not None:
                name = str(getattr(fr, "name", fr)).upper()
                if name not in _NORMAL_FINISH_REASONS:
                    return name
    except Exception:  # noqa: BLE001 — never let block detection break the round.
        return None
    return None


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
    def supports_tools(self) -> bool:
        """False (F120): GeminiProvider defines only complete()/stream() and inherits
        the base complete_with_tools (which ignores tool_schemas). The native tool
        loop is deferred to T7; until it lands the selector must route an agentic
        turn AWAY from Gemini (or floor honestly) rather than silently degrade.

        T7 (DEFERRED — own epic): port a native ``complete_with_tools`` reusing the
        shipped spine (LoopGuard, parse_react_action, decide_nudge/
        synthesize_from_calls, is_consequential_giveup_now, summarize_tool_outcomes/
        TOOL_FAILED_MARKER, WRAPUP_DIRECTIVE, truncate_observation/
        trim_messages_to_budget, validate_resume_transcript [+provider_kind="gemini"],
        on_iteration_complete/ReActIterationState, _record_usage_safe) with only the
        google-genai function-calling wire adapter new; then flip this to True with
        its own agentic-Gemini journey. NOT built in C2 (keeps it a focused
        resilience fix).
        """
        return False

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
            # NOTE: max_output_tokens' 250000 default exceeds real Anthropic
            # per-model ceilings (8192-64000) — safe today (no Anthropic
            # provider configured), but the FIRST Anthropic provider added
            # must set an explicit models[].max_output_tokens (or a smaller
            # provider-level max_output_tokens) or its first real request
            # fails with a 400. No window-bounding exists for this
            # provider (unlike OpenAI's _output_cap) — deliberately out of
            # scope for this plan.
            max_output_tokens=_max_tokens(kwargs, default=resolve_model_override(self._config, resolved_model)[0]),
        )
        _t0 = time.monotonic()
        # F119 — accumulate the LAST non-None usage_metadata across chunks and
        # record the streamed round's spend at generator exit (best-effort,
        # fail-open) so streaming no longer under-counts the per-turn budget.
        final_usage: Any = None
        yielded_any = False
        last_chunk: Any = None
        try:
            try:
                async for chunk in await self._client.aio.models.generate_content_stream(
                    model=resolved_model,
                    contents=contents,
                    config=config,
                ):
                    last_chunk = chunk
                    chunk_usage = getattr(chunk, "usage_metadata", None)
                    if chunk_usage is not None:
                        final_usage = chunk_usage
                    if chunk.text:
                        yielded_any = True
                        yield chunk.text
            finally:
                await self._record_stream_usage_safe(
                    final_usage, resolved_model, (time.monotonic() - _t0) * 1000
                )
        except Exception as exc:
            log.engine.error(
                "[gemini] stream: error",
                exc_info=exc,
                extra={"_fields": {"provider": self._name}},
            )
            raise ProviderError(self._name, exc) from exc
        # F-23 — a stream that ended having yielded NOTHING because the generation
        # was safety/recitation-blocked must surface honestly, not finish as a
        # silent empty success. (Raised OUTSIDE the wrap above so it is not
        # double-wrapped.) A legitimately empty (non-blocked) generation is left as-is.
        if not yielded_any:
            reason = _block_reason(last_chunk)
            if reason is not None:
                log.engine.warning(
                    "[gemini] stream: generation blocked — surfacing honestly",
                    extra={"_fields": {"provider": self._name, "reason": reason}},
                )
                raise ProviderError(self._name, ValueError(f"generation blocked: {reason}"))
        log.engine.debug("[gemini] stream: exit", extra={"_fields": {"provider": self._name}})

    async def _record_stream_usage_safe(
        self, usage: Any, model: str, duration_ms: float
    ) -> None:
        """Record one streamed round's cost from the final usage_metadata (F119).

        Mirrors ``complete()``'s reads (prompt_token_count / candidates_token_count).
        Fail-open (B5): a missing/odd-shaped usage records NOTHING and logs at DEBUG
        — never break the streamed reply.
        """
        if usage is None:
            log.engine.debug(
                "[gemini] stream: no usage_metadata — recording nothing",
                extra={"_fields": {"provider": self._name}},
            )
            return
        try:
            in_tok = int(getattr(usage, "prompt_token_count", 0) or 0)
            out_tok = int(getattr(usage, "candidates_token_count", 0) or 0)
        except Exception as exc:  # B5 — never break the stream on cost accounting.
            log.engine.debug(
                "[gemini] stream: usage extraction skipped",
                extra={"_fields": {"provider": self._name, "err": str(exc)}},
            )
            return
        await self._record_cost(
            model=model, input_tokens=in_tok, output_tokens=out_tok, duration_ms=duration_ms,
        )

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
            # NOTE: max_output_tokens' 250000 default exceeds real Anthropic
            # per-model ceilings (8192-64000) — safe today (no Anthropic
            # provider configured), but the FIRST Anthropic provider added
            # must set an explicit models[].max_output_tokens (or a smaller
            # provider-level max_output_tokens) or its first real request
            # fails with a 400. No window-bounding exists for this
            # provider (unlike OpenAI's _output_cap) — deliberately out of
            # scope for this plan.
            max_output_tokens=_max_tokens(kwargs, default=resolve_model_override(self._config, resolved_model)[0]),
        )
        async def _round() -> Any:
            return await self._client.aio.models.generate_content(
                model=resolved_model,
                contents=contents,
                config=config,
            )

        try:
            # F115 — record the per-round HTTP outcome onto the registry-owned breaker.
            response = await self._resilient_round(_round)
        except Exception as exc:
            log.engine.error(
                "[gemini] complete: error",
                exc_info=exc,
                extra={"_fields": {"provider": self._name}},
            )
            raise ProviderError(self._name, exc) from exc
        duration_ms = (time.monotonic() - t0) * 1000
        text = response.text or ""
        # F-23 — ``response.text or ""`` returns "" for BOTH a transient empty
        # generation AND a safety/recitation BLOCK; do not coerce either into a
        # silent empty success. A confirmed block surfaces honestly (ProviderError
        # → the gateway floors); a plain empty generation gets ONE retry as a cheap
        # backstop (parity with the OpenAI/Anthropic siblings).
        if not text.strip():
            reason = _block_reason(response)
            if reason is not None:
                log.engine.warning(
                    "[gemini] complete: generation blocked — surfacing honestly",
                    extra={"_fields": {"provider": self._name, "reason": reason}},
                )
                raise ProviderError(self._name, ValueError(f"generation blocked: {reason}"))
            log.engine.warning(
                "[gemini] complete: empty content — retrying once",
                extra={"_fields": {"provider": self._name, "model": resolved_model}},
            )
            try:
                retry = await self._resilient_round(_round)
            except Exception as exc:
                if not _is_transport_error(exc):
                    raise
                log.engine.warning(
                    "[gemini] complete: retry after empty failed — keeping empty",
                    exc_info=exc,
                    extra={"_fields": {"provider": self._name}},
                )
            else:
                response = retry
                text = response.text or ""
                reason2 = _block_reason(response)
                if not text.strip() and reason2 is not None:
                    log.engine.warning(
                        "[gemini] complete: retry also blocked — surfacing honestly",
                        extra={"_fields": {"provider": self._name, "reason": reason2}},
                    )
                    raise ProviderError(self._name, ValueError(f"generation blocked: {reason2}"))
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
