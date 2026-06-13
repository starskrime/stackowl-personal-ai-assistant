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
from stackowl.pipeline.giveup_floor import is_consequential_giveup_now
from stackowl.pipeline.persistence import TOOL_FAILED_MARKER, summarize_tool_outcomes
from stackowl.pipeline.supervisor import decide_nudge, synthesize_from_calls
from stackowl.providers._blocks import anthropic_user_content, message_has_blocks
from stackowl.providers._react import LoopGuard
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

    @property
    def supports_vision(self) -> bool:
        """True when the configured Claude model is vision-capable (Claude 3+)."""
        return is_vision_model(self._config.default_model)

    @property
    def supports_document(self) -> bool:
        """A vision-capable Claude model also accepts PDF document blocks (Mode B)."""
        return is_vision_model(self._config.default_model)

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
        on_iteration_complete: IterationCallback | None = None,
        resume_messages: list[dict[str, Any]] | None = None,
        resume_tool_calls: list[dict[str, Any]] | None = None,
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
                "resuming": resume_messages is not None,
            }},
        )
        # B1 — durable-ReAct resume seam.  When resume_messages is None (default,
        # always today) build the initial list from history + user_text exactly as
        # before.  When provided, seed the loop directly from the checkpoint
        # transcript — do NOT re-prepend history or user_text (they are already in
        # the transcript).  The system prompt always stays in system_kwargs
        # (separate from the messages list) regardless of the resume path, so
        # there is no double-injection risk for Anthropic.
        if resume_messages is not None:
            # Fail loud on a malformed transcript (empty / stray system turn /
            # dangling unanswered tool call / unmatched pair) BEFORE the first API
            # call, so the cause is a typed ResumeTranscriptError instead of an
            # opaque ProviderError 400.  Defense-in-depth: a well-formed checkpoint
            # (written after tool results are appended) never dangles; this guards
            # future/cross-provider/hand-crafted transcripts.
            validate_resume_transcript(resume_messages, provider_kind="anthropic")
            messages: list[dict[str, Any]] = list(resume_messages)
        else:
            history_dicts = [{"role": m.role, "content": m.content} for m in (history or [])]
            messages = [
                *history_dicts,
                {"role": "user", "content": user_text},
            ]
        system_kwargs: dict[str, Any] = {"system": system_text} if system_text else {}
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
                    "[anthropic] complete_with_tools: persistence_check raised — failing open to veto",
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
                consequential_giveup=is_consequential_giveup_now(),
            )
            if directive:
                log.engine.info(
                    "[anthropic] complete_with_tools: persistence nudge — continuing loop",
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
            # Bound total context BEFORE the call. Only tool_result CONTENT is
            # elided (never the message itself), so tool_use/tool_result pairing
            # stays valid for the Anthropic API.
            messages = trim_messages_to_budget(messages, budget)
            _t_call = time.monotonic()
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

            # E8-S0cost — record EACH internal tool-loop API round's spend (B5:
            # usage extraction is itself fail-open — a response shape without usage
            # must never break the loop).
            await self._record_usage_safe(response, (time.monotonic() - _t_call) * 1000)

            if response.stop_reason != "tool_use":
                text = "".join(b.text for b in response.content if hasattr(b, "text"))
                # W4.T17 — the iteration callback (steer drain + cooperative stop +
                # budget gate) runs BEFORE the give-up nudge at this final-answer
                # boundary. This closes three exit-path hazards: a TurnStopped /
                # BudgetBreach raised by the callback PROPAGATES (a user-stop / a
                # budget-kill is NOT a give-up — never nudge it), and a folded
                # live-steer message PRE-EMPTS the nudge (the user redirected;
                # re-nudging toward the OLD goal is wrong — steer wins). Only when
                # the callback neither raises nor folds does the give-up judge run.
                folded = (
                    await on_iteration_complete(
                        ReActIterationState(
                            iteration=_iter_idx,
                            messages=list(messages),
                            tool_call_records=list(all_calls),
                        )
                    )
                    if on_iteration_complete is not None
                    else None
                )
                if folded:
                    messages.extend(folded)
                    log.engine.info(
                        "[anthropic] complete_with_tools: steer folded at give-up "
                        "boundary — pre-empting give-up nudge",
                        extra={"_fields": {"provider": self._name}},
                    )
                    continue
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
            iter_directives: list[str] = []
            for b in response.content:
                if b.type != "tool_use":
                    continue
                result_text = await tool_dispatcher(b.name, dict(b.input))
                # Classify failure from the dispatcher's internal marker, then STRIP
                # it: the marker is a private signal channel — the tool_result content
                # sent to the API and the stored telemetry must only ever see clean
                # text (a NUL sentinel can corrupt HTTP/JSON payloads and text columns).
                failed = TOOL_FAILED_MARKER in result_text
                clean = result_text.replace(TOOL_FAILED_MARKER, "")
                # Cap the observation so a huge tool result can't overflow the context.
                capped = truncate_observation(clean)
                all_calls.append({"id": b.id, "name": b.name, "args": b.input, "result": capped, "failed": failed})
                tool_results.append({"type": "tool_result", "tool_use_id": b.id, "content": capped})
                directive = guard.observe(b.name, dict(b.input))
                if directive:
                    iter_directives.append(directive)
            messages.append({"role": "user", "content": tool_results})
            # S3 — fire per-iteration callback after tool calls + observations are
            # appended but BEFORE advancing to the next LLM round (or breaking).
            # Shallow-copy both lists so the callback cannot mutate loop state.
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
                    "[anthropic] complete_with_tools: loop guard tripped — "
                    "repeated identical calls, breaking to wrap-up",
                    extra={"_fields": {"provider": self._name}},
                )
                break
            if iter_directives:
                messages.append({"role": "user", "content": iter_directives[0]})

        log.engine.warning(
            "[anthropic] complete_with_tools: max_iterations reached",
            extra={"_fields": {"provider": self._name}},
        )
        # Phase F — graceful max-out: never return empty. Make ONE final model call
        # WITHOUT tools (keeping the system prompt) after a global, language-agnostic
        # wrap-up directive so the user always gets a coherent answer. Fail-open: any
        # provider error falls back to the last assistant text already gathered.
        # Capture the partial BEFORE appending WRAPUP_DIRECTIVE so the floor's
        # {partial} reflects real progress, never an echo of the directive.
        partial = _last_assistant_text(messages)
        try:
            messages = trim_messages_to_budget(messages, budget)
            messages.append({"role": "user", "content": WRAPUP_DIRECTIVE})
            _t_wrap = time.monotonic()
            wrapup = await self._client.messages.create(
                model=self._config.default_model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=self._config.max_output_tokens,
                **system_kwargs,
            )
            await self._record_usage_safe(wrapup, (time.monotonic() - _t_wrap) * 1000)
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
        fallback = partial
        if fallback.strip():
            return fallback, all_calls
        # W2.T9 — the wrap-up produced nothing AND no prior assistant text exists.
        # Never hand the user "" — synthesize the honest never-empty floor naming
        # the failed capability. synthesize_from_calls is pure and never returns
        # empty, so `floored` is guaranteed non-empty.
        log.engine.warning(
            "[anthropic] complete_with_tools: empty wrap-up — flooring honest answer",
            extra={"_fields": {"provider": self._name, "calls": len(all_calls)}},
        )
        floored = synthesize_from_calls(user_text, all_calls, partial)
        return floored, all_calls

    async def _record_usage_safe(self, response: Any, duration_ms: float) -> None:
        """Record one tool-loop round's cost from an Anthropic response (B5 fail-open).

        Extracting usage off the response is itself defensive: a response shape
        without ``usage``/``model`` (e.g. a scripted test fake) must NEVER break the
        tool loop, so any extraction error is logged and swallowed here. The actual
        record() call is further guarded inside ``_record_cost``.
        """
        try:
            usage = getattr(response, "usage", None)
            in_tok = usage.input_tokens if usage else 0
            out_tok = usage.output_tokens if usage else 0
            model = getattr(response, "model", "") or ""
        except Exception as exc:  # B5 — usage shape varies / may be absent on fakes.
            log.engine.debug(
                "[anthropic] _record_usage_safe: usage extraction skipped",
                extra={"_fields": {"provider": self._name, "err": str(exc)}},
            )
            return
        await self._record_cost(
            model=model, input_tokens=in_tok, output_tokens=out_tok, duration_ms=duration_ms,
        )

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        TestModeGuard.assert_not_test_mode("anthropic.complete")
        log.engine.debug(
            "[anthropic] complete: entry",
            extra={"_fields": {"provider": self._name, "model": model}},
        )
        t0 = time.monotonic()
        system_parts = [m.content for m in messages if m.role == "system"]
        # A message carrying image/document blocks is serialized to native content
        # blocks; a plain message keeps the cheap string form (B6 minimal change).
        chat_msgs = [
            {"role": m.role, "content": anthropic_user_content(m)}
            if message_has_blocks(m)
            else {"role": m.role, "content": m.content}
            for m in messages
            if m.role != "system"
        ]
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
        # E8-S0cost — single recording site: every provider call records its spend.
        await self._record_cost(
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
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
