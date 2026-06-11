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
from stackowl.pipeline.persistence import TOOL_FAILED_MARKER, summarize_tool_outcomes
from stackowl.pipeline.supervisor import decide_nudge
from stackowl.providers._blocks import message_has_blocks, openai_user_content
from stackowl.providers._react import LoopGuard, parse_react_action
from stackowl.providers._truncate import (
    CONTEXT_CHAR_BUDGET,
    trim_messages_to_budget,
    truncate_observation,
)
from stackowl.providers._wrapup import WRAPUP_DIRECTIVE
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.react_callback import IterationCallback, ReActIterationState
from stackowl.providers.resume_validation import validate_resume_transcript
from stackowl.providers.vision_models import is_vision_model


def _last_assistant_text(messages: list[dict[str, Any]]) -> str:
    """Last non-empty assistant text already in ``messages`` (newest first), or ""."""
    for m in reversed(messages):
        if m.get("role") == "assistant":
            content = m.get("content")
            if isinstance(content, str) and content.strip():
                return content
    return ""


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

    @property
    def supports_vision(self) -> bool:
        """True when the configured model is a known vision model.

        Covers cloud OpenAI (gpt-4o, …) AND a self-hosted Ollama vision tag
        (llava, llama3.2-vision, …) — both ride the OpenAI ``image_url`` data-URL
        serialization in ``complete()``.
        """
        return is_vision_model(self._config.default_model)

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
        on_iteration_complete: IterationCallback | None = None,
        resume_messages: list[dict[str, Any]] | None = None,
        resume_tool_calls: list[dict[str, Any]] | None = None,
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
                    "resuming": resume_messages is not None,
                }
            },
        )
        # B1 — durable-ReAct resume seam.  When resume_messages is None (default,
        # always today) build the initial list from system_text + catalog + history
        # + user_text exactly as before.  When provided, seed the loop directly
        # from the checkpoint transcript — the system message (including any
        # tool catalog) is already at messages[0] so we must NOT re-prepend it
        # (that would cause the double-injection the S5 caveat warns about).
        if resume_messages is not None:
            # Fail loud on a malformed transcript (empty / dangling unanswered
            # tool call / unmatched pair) BEFORE the first API call, so the cause
            # is a typed ResumeTranscriptError rather than an opaque ProviderError
            # 400.  Defense-in-depth: a well-formed checkpoint (written after tool
            # results are appended) never dangles; this guards future/cross-provider
            # /hand-crafted transcripts.
            validate_resume_transcript(resume_messages, provider_kind="openai")
            # B3-TODO (catalog staleness): on resume the text-protocol tool catalog
            # is embedded in messages[0] (system) from the ORIGINAL run.  If
            # tool_schemas have changed between the crashed run and this resume, that
            # embedded catalog is STALE.  Native tool_calls are unaffected — they use
            # the fresh `tools=` registry passed to create() below — so only the
            # weak-model text-protocol fallback (parse_react_action) sees stale tool
            # docs.  Revisit in B3 (re-render + splice messages[0] on resume).
            messages: list[dict[str, Any]] = list(resume_messages)
        else:
            history_dicts = [{"role": m.role, "content": m.content} for m in (history or [])]
            messages = []
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
        # B1 hardening — seed all_calls from the prior tool history on resume so the
        # returned records, the persistence give-up judge, and the iteration
        # callback all see prior+new work (not just post-resume calls).  Default
        # None => empty list (unchanged).
        all_calls: list[dict[str, Any]] = list(resume_tool_calls) if resume_tool_calls else []
        # Phase D — bounded persistence enforcement: at most 2 corrective nudges
        # per turn, so a stubborn give-up cannot explode cost/loops. The structural
        # veto (decide_nudge) is the backstop for a lying/erroring judge, and the
        # escalation-reward cap (calls_at_last_nudge) spends budget only on a
        # re-refusal, never on a turn where the model actually escalated.
        nudge_budget = 2
        calls_at_last_nudge: int | None = None

        async def _enforce(content: str) -> str | None:
            """Run the persistence check for a draft final answer (fail-OPEN).

            The judge verdict is fed through :func:`decide_nudge`, which applies
            the always-on structural veto (overriding a hallucinated/erroring
            DELIVERED on a structural give-up) and the escalation-reward budget
            rule. The judge itself fails OPEN — on any error it returns None and
            the veto then decides from the authoritative ``failed`` bools.
            """
            nonlocal nudge_budget, calls_at_last_nudge
            if persistence_check is None:
                return None
            try:
                judge_directive = await persistence_check(
                    content, summarize_tool_outcomes(all_calls)
                )
            except Exception as exc:  # fail OPEN — never block/loop on a judge error
                log.engine.error(
                    "[openai] complete_with_tools: persistence_check raised — failing open to veto",
                    exc_info=exc,
                    extra={"_fields": {"provider": self._name}},
                )
                judge_directive = None
            directive, nudge_budget, calls_at_last_nudge = decide_nudge(
                judge_directive=judge_directive,
                all_calls=all_calls,
                draft=content,
                nudge_budget=nudge_budget,
                calls_at_last_nudge=calls_at_last_nudge,
            )
            if directive:
                log.engine.info(
                    "[openai] complete_with_tools: persistence nudge — continuing loop",
                    extra={"_fields": {"provider": self._name, "nudge_budget": nudge_budget}},
                )
            return directive

        budget = (
            int(self._config.context_chars * 0.8)
            if self._config.context_chars
            else CONTEXT_CHAR_BUDGET
        )

        guard = LoopGuard()
        for _iter_idx in range(resolved_iterations):
            # Bound total context BEFORE the call: elide oldest tool observations
            # if the accumulated history would overflow the model's window.
            messages = trim_messages_to_budget(messages, budget)
            _t_call = time.monotonic()
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

            # E8-S0cost — record EACH internal tool-loop API round's spend (B5:
            # usage extraction is itself fail-open — a response shape without usage
            # must never break the loop).
            await self._record_usage_safe(response, (time.monotonic() - _t_call) * 1000)

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
                    # Classify failure from the dispatcher's internal marker, then
                    # STRIP it: the marker is a private signal channel — the model's
                    # OBSERVATION and stored telemetry must only ever see clean text
                    # (a NUL sentinel can corrupt HTTP/JSON payloads and text columns).
                    failed = TOOL_FAILED_MARKER in result_text
                    clean = result_text.replace(TOOL_FAILED_MARKER, "")
                    # Cap the observation so a huge tool result (e.g. a browser
                    # snapshot) can't blow the context window over iterations.
                    capped = truncate_observation(clean)
                    all_calls.append({"id": None, "name": name, "args": args, "result": capped, "failed": failed})
                    react_directive = guard.observe(name, args)
                    if guard.tripped():
                        log.engine.warning(
                            "[openai] complete_with_tools: loop guard tripped — "
                            "repeated identical calls, breaking to wrap-up",
                            extra={"_fields": {"provider": self._name}},
                        )
                        messages.append({"role": "user", "content": f"OBSERVATION: {capped}"})
                        # S3 — guard trip on ReAct path: fire before break so
                        # the checkpoint captures the observation that caused it.
                        if on_iteration_complete is not None:
                            folded = await on_iteration_complete(
                                ReActIterationState(
                                    iteration=_iter_idx,
                                    messages=list(messages),
                                    tool_call_records=list(all_calls),
                                )
                            )
                            if folded:
                                messages.extend(folded)
                        break
                    messages.append({"role": "user", "content": f"OBSERVATION: {capped}"})
                    if react_directive:
                        messages.append({"role": "user", "content": react_directive})
                    # S3 — fire per-iteration callback after OBSERVATION appended
                    # (ReAct text path), before continuing to the next LLM round.
                    # Task 9 — fold any returned steering messages into the live
                    # list so the next LLM round observes them.
                    if on_iteration_complete is not None:
                        folded = await on_iteration_complete(
                            ReActIterationState(
                                iteration=_iter_idx,
                                messages=list(messages),
                                tool_call_records=list(all_calls),
                            )
                        )
                        if folded:
                            messages.extend(folded)
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
                # S3 — fire per-iteration callback for this terminal iteration
                # (the final answer round, no tool calls), then return.  Task 9 —
                # fold for contract uniformity; this is the terminal round so a
                # fold here is not re-sent, but we never silently drop a return.
                if on_iteration_complete is not None:
                    folded = await on_iteration_complete(
                        ReActIterationState(
                            iteration=_iter_idx,
                            messages=list(messages),
                            tool_call_records=list(all_calls),
                        )
                    )
                    if folded:
                        messages.extend(folded)
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
            iter_native_directives: list[str] = []
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
                    err = truncate_observation(f"ERROR: could not parse arguments for {fn_name}: {exc}")
                    # A malformed-args dispatch is always a failure for the judge.
                    all_calls.append({"id": tc.id, "name": fn_name, "args": {}, "result": err, "failed": True})
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": err})
                    continue
                result_text = await tool_dispatcher(fn_name, args)
                # Classify then STRIP the internal failure marker — model/telemetry
                # only ever see clean text; failure travels as the typed flag below.
                failed = TOOL_FAILED_MARKER in result_text
                clean = result_text.replace(TOOL_FAILED_MARKER, "")
                # Cap the observation so a huge tool result can't overflow the context.
                capped = truncate_observation(clean)
                all_calls.append({"id": tc.id, "name": fn_name, "args": args, "result": capped, "failed": failed})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": capped})
                directive = guard.observe(fn_name, args)
                if directive:
                    iter_native_directives.append(directive)
            # S3 — fire per-iteration callback after all native tool results are
            # appended (before guard check / directives), so the checkpoint captures
            # the complete state of this iteration including all tool observations.
            # Task 9 — fold any returned steering messages into the live list so
            # the next LLM round observes them.
            if on_iteration_complete is not None:
                folded = await on_iteration_complete(
                    ReActIterationState(
                        iteration=_iter_idx,
                        messages=list(messages),
                        tool_call_records=list(all_calls),
                    )
                )
                if folded:
                    messages.extend(folded)
            if guard.tripped():
                log.engine.warning(
                    "[openai] complete_with_tools: loop guard tripped — "
                    "repeated identical calls, breaking to wrap-up",
                    extra={"_fields": {"provider": self._name}},
                )
                break
            if iter_native_directives:
                messages.append({"role": "user", "content": iter_native_directives[0]})

        log.engine.warning(
            "[openai] complete_with_tools: max_iterations reached",
            extra={"_fields": {"provider": self._name}},
        )
        # Phase F — graceful max-out: never return empty. Make ONE final model call
        # WITHOUT tools after a global, language-agnostic wrap-up directive so the
        # user always gets a coherent answer (best result + remaining blocker +
        # next step). Fail-open: any provider error falls back to the last assistant
        # text already gathered, so a hard failure never produces silence here.
        try:
            messages = trim_messages_to_budget(messages, budget)
            messages.append({"role": "user", "content": WRAPUP_DIRECTIVE})
            _t_wrap = time.monotonic()
            wrapup = await self._client.chat.completions.create(
                model=resolved_model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=self._config.max_output_tokens,
            )
            await self._record_usage_safe(wrapup, (time.monotonic() - _t_wrap) * 1000)
            text = wrapup.choices[0].message.content or ""
            if text.strip():
                log.engine.debug(
                    "[openai] complete_with_tools: wrap-up answer delivered at max-out",
                    extra={"_fields": {"provider": self._name, "len": len(text)}},
                )
                return text, all_calls
        except Exception as exc:  # fail-open — never surface silence on a wrap-up error
            log.engine.error(
                "[openai] complete_with_tools: wrap-up call failed — falling back",
                exc_info=exc,
                extra={"_fields": {"provider": self._name}},
            )
        fallback = _last_assistant_text(messages)
        if fallback.strip():
            return fallback, all_calls
        return "", all_calls

    async def _record_usage_safe(self, response: Any, duration_ms: float) -> None:
        """Record one tool-loop round's cost from an OpenAI response (B5 fail-open).

        Extracting usage off the response is itself defensive: a response shape
        without ``usage``/``model`` (e.g. a scripted test fake) must NEVER break the
        tool loop, so any extraction error is logged and swallowed here. The actual
        record() call is further guarded inside ``_record_cost``.
        """
        try:
            usage = getattr(response, "usage", None)
            in_tok = usage.prompt_tokens if usage else 0
            out_tok = usage.completion_tokens if usage else 0
            model = getattr(response, "model", "") or ""
        except Exception as exc:  # B5 — usage shape varies / may be absent on fakes.
            log.engine.debug(
                "[openai] _record_usage_safe: usage extraction skipped",
                extra={"_fields": {"provider": self._name, "err": str(exc)}},
            )
            return
        await self._record_cost(
            model=model, input_tokens=in_tok, output_tokens=out_tok, duration_ms=duration_ms,
        )

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        TestModeGuard.assert_not_test_mode("openai.complete")
        log.engine.debug(
            "[openai] complete: entry",
            extra={"_fields": {"provider": self._name, "model": model}},
        )
        t0 = time.monotonic()
        resolved_model = model or self._config.default_model
        # A message with image blocks → multimodal content parts (data-URL image);
        # a plain message keeps the cheap string form (B6 minimal change).
        oai_msgs = [
            {"role": m.role, "content": openai_user_content(m)}
            if message_has_blocks(m)
            else {"role": m.role, "content": m.content}
            for m in messages
        ]
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
        # E8-S0cost — single recording site: every provider call records its spend.
        await self._record_cost(
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
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
