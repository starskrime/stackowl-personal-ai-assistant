"""OpenAIProvider — ModelProvider for OpenAI and all OpenAI-compatible endpoints.

Covers: OpenAI, Groq, Together, Mistral, Perplexity, DeepSeek, Ollama, etc.
Adding a new compatible provider requires only a new stackowl.yaml entry — zero new code.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal

import openai

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import ProviderError, TurnStopped
from stackowl.infra.net.host_locality import is_local_url
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.delivery_gate import is_consequential_giveup_now
from stackowl.pipeline.persistence import TOOL_FAILED_MARKER, summarize_tool_outcomes
from stackowl.pipeline.supervisor import decide_nudge, synthesize_from_calls
from stackowl.providers._blocks import message_has_blocks, openai_user_content
from stackowl.providers._react import LoopGuard, looks_like_tool_call, parse_react_action
from stackowl.providers._truncate import (
    CONTEXT_CHAR_BUDGET,
    trim_messages_to_budget,
    truncate_observation,
)
from stackowl.providers._wrapup import FORMAT_FIX_DIRECTIVE, WRAPUP_DIRECTIVE
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.escalation_signal import escalation_requested
from stackowl.providers.llm_gateway import ESCALATE_SENTINEL
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


_THINK_RE = re.compile(
    r"<think>.*?</think>|<thought>.*?</thought>", re.DOTALL | re.IGNORECASE
)
# Known reasoning-block tag conventions across model families: qwen3-style
# <think> and Gemini-style <thought> (NeraAiRaw is confirmed Gemini-family —
# see docs/nera-gateway-tool-calling-gap.md). Live incident 2026-07-16: a
# <thought>...</thought> block shipped raw because only <think> was recognized.
_THINK_OPEN_TAGS = ("<think>", "<thought>")


def strip_think(text: str) -> str:
    """Remove chain-of-thought reasoning blocks (``<think>`` or ``<thought>``)
    from output.

    Reasoning models emit chain-of-thought inside a reasoning tag before the
    answer; structured/utility callers (judge, fact extractor) never want it.
    Also handles a response TRUNCATED mid-thinking — an unclosed opening tag left
    when the output-token cap is hit — by dropping everything from that dangling
    tag onward. That truncation is exactly what left ``content`` empty and
    crashed the fact extractor / fooled the judge into failing open.
    """
    if not text:
        return ""
    cleaned = _THINK_RE.sub("", text)
    lowered = cleaned.lower()
    indices = [i for tag in _THINK_OPEN_TAGS if (i := lowered.find(tag)) != -1]
    if indices:  # unclosed (truncated) block — the rest is all reasoning
        cleaned = cleaned[: min(indices)]
    return cleaned.strip()


_THINK_TAGS: tuple[tuple[str, str], ...] = (
    ("<think>", "</think>"),
    ("<thought>", "</thought>"),
)


class _ThinkStreamFilter:
    """Stateful reasoning-block filter for a LIVE token stream.

    ``strip_think()`` above only works on a complete string — a streamed delta
    can split an opening/closing tag across multiple chunks, so a per-chunk
    regex sub would miss it. ``stream()`` (unlike this provider's non-streaming
    ``complete()``/``complete_with_tools()``, which already call strip_think())
    never filtered this at all — a live reasoning trace on a plain
    conversational turn streamed straight to the user as the "answer".

    A reasoning block always OPENS at the very start of a completion (models
    never interleave it mid-answer), so only the opening boundary needs
    buffering: once the reply is confirmed not to start with any known
    reasoning tag (see ``_THINK_TAGS``), every later delta passes through
    unfiltered — no unbounded buffering.
    """

    def __init__(self) -> None:
        self._state = "sniffing"  # sniffing -> (suppressing ->) passthrough
        self._pending = ""
        self._close_tag = ""

    def feed(self, delta: str) -> str:
        if self._state == "passthrough":
            return delta
        self._pending += delta
        if self._state == "sniffing":
            stripped = self._pending.lstrip()
            if not stripped:
                return ""  # only whitespace so far — keep sniffing
            lowered = stripped.lower()
            matched = next(
                (pair for pair in _THINK_TAGS if lowered.startswith(pair[0])), None
            )
            if matched is not None:
                open_tag, self._close_tag = matched
                self._pending = stripped[len(open_tag):]
                self._state = "suppressing"
            elif any(
                len(lowered) < len(open_tag) and open_tag.startswith(lowered)
                for open_tag, _ in _THINK_TAGS
            ):
                return ""  # ambiguous prefix (any candidate tag) — need more chars
            else:
                self._state = "passthrough"
                out, self._pending = self._pending, ""
                return out
        # state == "suppressing" (falls through here in the same feed() call
        # when a single delta contains both the opening and closing tag)
        close_idx = self._pending.lower().find(self._close_tag)
        if close_idx == -1:
            return ""  # still inside the block — emit nothing
        rest = self._pending[close_idx + len(self._close_tag):]
        self._pending = ""
        self._state = "passthrough"
        return rest

    def flush(self) -> str:
        """Call once the stream ends. An unclosed ``<think>`` (truncated mid-
        reasoning, e.g. output-token cap hit) means everything buffered is
        reasoning, not answer — drop it, matching strip_think()'s truncation
        policy. Otherwise (stream ended before sniffing resolved) return
        whatever was buffered so a short real reply is never lost."""
        if self._state == "suppressing":
            self._pending = ""
            return ""
        out, self._pending = self._pending, ""
        return out


# F027 extension — the in-loop tool round had NO app-level timeout (only the
# terminal wrap-up did), so a hung SDK call could stall a turn for however long
# the openai SDK's own default takes (minutes). Bounded by the SAME residual
# budget threaded in as wrapup_deadline_s when the caller (execute.py's
# BudgetGovernor) supplies one; this fallback covers a non-budgeted caller.
_ROUND_DEADLINE_FALLBACK_S = 120.0


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
    def _is_local_backend(self) -> bool:
        """True when configured against a loopback/private base_url (e.g. Ollama).

        Locality-aware pricing (F128): an unknown model served by THIS local
        backend stays $0; the same unknown model on a cloud endpoint is charged a
        conservative fallback. Uses the authoritative ``is_local_url`` classifier
        over the configured ``base_url`` (None → cloud OpenAI).
        """
        return is_local_url(self._config.base_url)

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
        _t0 = time.monotonic()
        # F119 — request the trailing usage chunk so the streamed round records cost
        # via the single recording site (was unrecorded → a budget bypass on cheap
        # conversational turns). Ollama/compatible endpoints may ignore/omit it →
        # tolerated below (record nothing, debug log, never break the stream).
        final_usage: Any = None
        final_model: str = resolved_model
        think_filter = _ThinkStreamFilter()
        try:
            stream_resp = await self._client.chat.completions.create(  # type: ignore[call-overload]
                model=resolved_model,
                messages=oai_msgs,
                max_tokens=_max_tokens(kwargs, default=self._config.max_output_tokens),
                stream=True,
                stream_options={"include_usage": True},
                **self._ollama_extra_body(resolved_model),
            )
            try:
                async for chunk in stream_resp:
                    # The trailing empty-choices chunk carries .usage (include_usage).
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage is not None:
                        final_usage = chunk_usage
                        final_model = getattr(chunk, "model", resolved_model) or resolved_model
                    delta_obj = chunk.choices[0].delta if chunk.choices else None
                    delta = getattr(delta_obj, "content", None) if delta_obj else None
                    # NeraAiRaw (post gateway-fix 2026-07-16): reasoning now streams via
                    # its OWN structured `delta.reasoning_content` field — never inline
                    # in `delta.content` — so the ACTION:/<thought> leak-guards above
                    # rarely see it anymore. But `delta.content` is empty/absent for the
                    # ENTIRE reasoning phase now, and this loop used to `continue`
                    # (yielding nothing) for every such chunk — silence for as long as
                    # the model reasons, which OwlResourceGuard's per-chunk timeout
                    # (guards.py) reads as a dead/hung provider and kills at the
                    # ceiling. A reasoning delta must still reset that clock even
                    # though it has nothing displayable to yield.
                    if not delta:
                        if getattr(delta_obj, "reasoning_content", None) if delta_obj else None:
                            yield ""
                        continue
                    # NOTE (2026-07-16): a standalone whitespace-only SSE delta used to
                    # collapse to a single space here — a workaround for a gateway
                    # framing bug (junk "\n\n\n" splitting real tokens mid-identifier).
                    # The gateway operator fixed that bug the same night; collapsing
                    # every whitespace-only delta to a space now DESTROYS legitimate
                    # newlines (list items, paragraph breaks) the fixed gateway
                    # correctly streams as their own delta, flattening every multi-line
                    # reply onto one line. Removed — pass whitespace-only deltas through
                    # verbatim, same as any other content. _NATIVE_CALL_RE (_react.py)
                    # already tolerates whitespace/newlines around a leaked call's
                    # separators independent of this, so leak detection is unaffected.
                    visible = think_filter.feed(delta)
                    # Yield unconditionally, even when suppressed (empty string):
                    # OwlResourceGuard's per-chunk timeout (guards.py) resets its
                    # clock on every __anext__() this generator yields — a raw
                    # delta arriving during a long-but-legitimate reasoning block
                    # (all suppressed to "" by think_filter) must still reset that
                    # clock, or a real, actively-generating model gets killed at
                    # the timeout ceiling and misread as a hung/dead provider.
                    yield visible
                tail = think_filter.flush()
                if tail:
                    yield tail
            finally:
                # B5/F119 — record consumed spend even if the consumer abandoned the
                # stream early (OwlTimeout/disconnect): best-effort, fail-open.
                await self._record_stream_usage_safe(
                    final_usage, final_model, (time.monotonic() - _t0) * 1000
                )
        except openai.APIError as exc:
            log.engine.error(
                "[openai] stream: API error",
                exc_info=exc,
                extra={"_fields": {"provider": self._name}},
            )
            raise ProviderError(self._name, exc) from exc
        log.engine.debug("[openai] stream: exit", extra={"_fields": {"provider": self._name}})

    async def _record_stream_usage_safe(
        self, usage: Any, model: str, duration_ms: float
    ) -> None:
        """Record one streamed round's cost from the trailing usage chunk (F119).

        Ollama-tolerant: a missing usage chunk (``usage`` is None) or an unexpected
        shape records NOTHING and logs at DEBUG (NOT error) — the streamed reply
        already happened and must never be broken by cost accounting (B5).
        """
        if usage is None:
            log.engine.debug(
                "[openai] stream: no usage chunk — recording nothing (ollama/compatible)",
                extra={"_fields": {"provider": self._name}},
            )
            return
        try:
            in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
            out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
        except Exception as exc:  # B5 — usage shape varies; never break the stream.
            log.engine.debug(
                "[openai] stream: usage extraction skipped",
                extra={"_fields": {"provider": self._name, "err": str(exc)}},
            )
            return
        await self._record_cost(
            model=model, input_tokens=in_tok, output_tokens=out_tok, duration_ms=duration_ms,
        )

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
        wrapup_deadline_s: float | None = None,
        can_escalate: bool = False,
    ) -> tuple[str, list[dict[str, Any]]]:
        """OpenAI function-calling tool-use loop.

        ``can_escalate`` (set ONLY by LLMGateway below the ceiling tier): when the
        model persistently emits an unparseable tool call (a leak) or spins on
        repeated failing calls, this loop returns the ESCALATE sentinel so the
        gateway re-runs on a stronger tier — instead of leaking the raw tool-call
        text or flooring. Default False ⇒ unchanged behaviour (honest floor)."""
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
            # B3-TODO (catalog staleness): applies ONLY to supports_native_tools=False
            # providers (the default True path embeds no catalog, so nothing to stale).
            # On resume the text-protocol tool catalog is embedded in messages[0]
            # (system) from the ORIGINAL run.  If
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
            # text, so weak models WITHOUT native tool_calls can still act (parsed back
            # by parse_react_action below). When the endpoint supports native tool-calls
            # (the default — every modern Ollama/OpenAI-compatible model), injecting this
            # catalog is pure interference: the model obeys the prompt and emits a bare-
            # JSON call as message content that can't be dispatched, so the call is
            # bounced and the turn fails. Skip it; rely on the native `tools=` registry
            # passed to create() below. The parser still runs as a fallback if a native
            # call is ever absent.
            inject_catalog = bool(tool_schemas) and not self._config.supports_native_tools
            catalog = _render_tool_catalog(tool_schemas) if inject_catalog else ""
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
        nudges_issued = 0

        async def _enforce(content: str) -> str | None:
            """Run the persistence check for a draft final answer (fail-OPEN).

            The judge verdict is fed through :func:`decide_nudge`, which applies
            the always-on structural veto (overriding a hallucinated/erroring
            DELIVERED on a structural give-up) and the escalation-reward budget
            rule. The judge itself fails OPEN — on any error it returns None and
            the veto then decides from the authoritative ``failed`` bools.
            """
            nonlocal nudge_budget, calls_at_last_nudge, nudges_issued
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
                consequential_giveup=is_consequential_giveup_now(),
                nudges_issued=nudges_issued,
            )
            if directive:
                nudges_issued += 1
                log.engine.info(
                    "[openai] complete_with_tools: persistence nudge — continuing loop",
                    extra={
                        "_fields": {
                            "provider": self._name,
                            "nudge_budget": nudge_budget,
                            "nudges_issued": nudges_issued,
                        }
                    },
                )
            return directive

        budget = (
            int(self._config.context_chars * 0.8)
            if self._config.context_chars
            else CONTEXT_CHAR_BUDGET
        )

        guard = LoopGuard()
        # Known tool names — lets the ReAct parser validate/repair a flattened-newline
        # name (e.g. "skill_managen" → "skill_manage") and reject a hallucinated one.
        _known_tools = {
            s["function"]["name"]
            for s in tool_schemas
            if isinstance(s, dict) and isinstance(s.get("function"), dict)
            and isinstance(s["function"].get("name"), str)
        }
        _fmt_fix_count = 0  # bounded re-prompts when a final answer leaks as a tool call
        _MAX_FORMAT_FIX = 2
        for _iter_idx in range(resolved_iterations):
            # PA3 — a circuit breaker opened on a PRIOR iteration's dispatch (the
            # pipeline set the turn-scoped escalation flag). Escalate to a stronger
            # tier instead of dead-ending. No-op for pinned owls (can_escalate
            # False ⇒ byte-identical) and at the ceiling (falls through to floor).
            if can_escalate and escalation_requested():
                log.engine.warning(
                    "[openai] complete_with_tools: circuit-open this turn — escalating to a stronger tier",
                    extra={"_fields": {"provider": self._name}},
                )
                return ESCALATE_SENTINEL, all_calls
            # Bound total context BEFORE the call: elide oldest tool observations
            # if the accumulated history would overflow the model's window.
            messages = trim_messages_to_budget(messages, budget)
            _t_call = time.monotonic()

            async def _round(_msgs: list[dict[str, Any]] = messages) -> Any:
                return await self._client.chat.completions.create(
                    model=resolved_model,
                    messages=_msgs,  # type: ignore[arg-type]
                    max_tokens=self._output_cap(resolved_model),
                    tools=tool_schemas,  # type: ignore[arg-type]
                    **self._ollama_extra_body(resolved_model),
                )

            async with TraceContext.span("provider.round"):
                try:
                    # F115 — per-round breaker/limiter site (NOT a wrap of the whole loop,
                    # which floors and would feed the breaker a false success).
                    # F027 extension — bound the round itself; a hung SDK call must
                    # never stall the turn past the residual budget (or the fallback
                    # when no budget was threaded in).
                    response = await asyncio.wait_for(
                        self._resilient_round(_round),
                        timeout=wrapup_deadline_s
                        if wrapup_deadline_s is not None
                        else _ROUND_DEADLINE_FALLBACK_S,
                    )
                except openai.APIError as exc:
                    log.engine.error(
                        "[openai] complete_with_tools: API error",
                        exc_info=exc,
                        extra={"_fields": {
                            "provider": self._name,
                            "duration_ms": (time.monotonic() - _t_call) * 1000,
                        }},
                    )
                    raise ProviderError(self._name, exc) from exc
                except TimeoutError as exc:
                    log.engine.error(
                        "[openai] complete_with_tools: round exceeded deadline — hung provider call",
                        exc_info=exc,
                        extra={"_fields": {
                            "provider": self._name,
                            "duration_ms": (time.monotonic() - _t_call) * 1000,
                        }},
                    )
                    raise ProviderError(self._name, exc) from exc

                round_duration_ms = (time.monotonic() - _t_call) * 1000
                log.engine.debug(
                    "[openai] complete_with_tools: round ok",
                    extra={"_fields": {
                        "provider": self._name, "iteration": _iter_idx,
                        "duration_ms": round_duration_ms,
                    }},
                )

            # E8-S0cost — record EACH internal tool-loop API round's spend (B5:
            # usage extraction is itself fail-open — a response shape without usage
            # must never break the loop).
            await self._record_usage_safe(response, round_duration_ms)

            if not response.choices:
                raise ProviderError(self._name, ValueError("empty choices"))
            choice = response.choices[0]
            if not choice.message.tool_calls:
                # Strip the reasoning trace at the boundary: the model thinks for
                # quality, but the <think> block is never used — discard it so it is
                # never parsed, stored in history, re-fed, or shown to the user.
                content = strip_think(choice.message.content or "")
                action = parse_react_action(content, known=_known_tools)
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
                # no action -> draft final answer.
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
                        "[openai] complete_with_tools: steer folded at give-up "
                        "boundary — pre-empting give-up nudge",
                        extra={"_fields": {"provider": self._name}},
                    )
                    continue
                # LEAK GUARD: the "final answer" is actually an unparsed tool call
                # (an ACTION block / bare JSON we couldn't dispatch). NEVER deliver
                # that raw text to the user. Re-prompt the exact format a bounded
                # number of times; if it persists, ESCALATE to a stronger tier when
                # allowed (the gateway re-runs), else fall to the honest floor.
                if looks_like_tool_call(content, known=_known_tools):
                    if _fmt_fix_count < _MAX_FORMAT_FIX:
                        _fmt_fix_count += 1
                        log.engine.warning(
                            "[openai] complete_with_tools: final answer looks like an "
                            "unparsed tool call — re-prompting the ACTION format",
                            extra={"_fields": {"provider": self._name, "attempt": _fmt_fix_count}},
                        )
                        messages.append({"role": "user", "content": FORMAT_FIX_DIRECTIVE})
                        continue
                    if can_escalate:
                        log.engine.warning(
                            "[openai] complete_with_tools: persistent tool-call leak — "
                            "escalating to a stronger tier",
                            extra={"_fields": {"provider": self._name}},
                        )
                        return ESCALATE_SENTINEL, all_calls
                    log.engine.warning(
                        "[openai] complete_with_tools: persistent tool-call leak, no "
                        "escalation available — honest floor instead of raw tool call",
                        extra={"_fields": {"provider": self._name}},
                    )
                    return synthesize_from_calls(user_text, all_calls, ""), all_calls
                # Phase D: before accepting it, ask the persistence judge whether
                # the agent delivered or gave up.
                directive = await _enforce(content)
                if directive:
                    # The judge ruled give-up. If a stronger tier is available,
                    # escalate the whole turn instead of nudging the weak model to
                    # try again — the objective verdict, not the model's self-report,
                    # drives the step-up. At the top tier (can_escalate False) we
                    # keep nudging as before.
                    if can_escalate:
                        log.engine.warning(
                            "[openai] complete_with_tools: judge ruled give-up — "
                            "escalating to a stronger tier instead of nudging",
                            extra={"_fields": {"provider": self._name}},
                        )
                        return ESCALATE_SENTINEL, all_calls
                    messages.append({"role": "user", "content": directive})
                    continue
                log.engine.debug(
                    "[openai] complete_with_tools: exit",
                    extra={"_fields": {"provider": self._name, "calls": len(all_calls)}},
                )
                return content, all_calls

            # Append assistant turn with tool_calls — strip the <think> trace so it
            # never bloats history or gets re-fed (the model reasons; we keep only
            # the answer). None when nothing but reasoning was emitted.
            messages.append({
                "role": "assistant",
                "content": strip_think(choice.message.content or "") or None,
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

        # Auto-escalate on max-out: the current tier burned its WHOLE tool budget
        # without delivering — an objective "out of its depth" signal. A weak model
        # never self-reports ESCALATE, so escalate here and hand the turn to the
        # next tier rather than wrapping up a weak answer on the weak model. The
        # gateway re-runs the turn one tier up; at the top tier can_escalate is
        # False, so the graceful wrap-up floor below still applies.
        if can_escalate:
            log.engine.warning(
                "[openai] complete_with_tools: tool budget exhausted without "
                "delivering — escalating to a stronger tier",
                extra={"_fields": {"provider": self._name, "calls": len(all_calls)}},
            )
            return ESCALATE_SENTINEL, all_calls

        log.engine.warning(
            "[openai] complete_with_tools: max_iterations reached",
            extra={"_fields": {"provider": self._name}},
        )
        # Phase F — graceful max-out: never return empty. Make ONE final model call
        # WITHOUT tools after a global, language-agnostic wrap-up directive so the
        # user always gets a coherent answer (best result + remaining blocker +
        # next step). Fail-open: any provider error falls back to the last assistant
        # text already gathered, so a hard failure never produces silence here.
        # Capture the partial BEFORE appending WRAPUP_DIRECTIVE so the floor's
        # {partial} reflects real progress, never an echo of the directive.
        partial = _last_assistant_text(messages)
        try:
            messages = trim_messages_to_budget(messages, budget)
            messages.append({"role": "user", "content": WRAPUP_DIRECTIVE})
            _t_wrap = time.monotonic()

            async def _wrap_round() -> Any:
                return await self._client.chat.completions.create(
                    model=resolved_model,
                    messages=messages,  # type: ignore[arg-type]
                    max_tokens=self._config.max_output_tokens,
                    **self._ollama_extra_body(resolved_model),
                )

            # F027 — bound the terminal wrap-up by the governor's residual budget so
            # a hung wrap-up cannot exceed the promised turn ceiling. None → today's
            # behavior, byte-identical (no wait_for). The bound is a wall-clock
            # wait_for around the await (a post-hoc check cannot bound a hang).
            if wrapup_deadline_s is not None:
                wrapup = await asyncio.wait_for(
                    self._resilient_round(_wrap_round), timeout=wrapup_deadline_s,
                )
            else:
                wrapup = await self._resilient_round(_wrap_round)
            await self._record_usage_safe(wrapup, (time.monotonic() - _t_wrap) * 1000)
            if not wrapup.choices:
                raise ProviderError(self._name, ValueError("empty choices"))
            text = strip_think(wrapup.choices[0].message.content or "")
            if text.strip():
                # F026 — the terminal wrap-up bypasses the in-loop judge/veto. Apply
                # the SAME structural give-up gate here: at max-out there is no loop
                # to continue (so NO nudge) and no extra network call (so NO LLM
                # judge) — the pure, synchronous predicate decides. On a dishonest
                # give-up (a consequential action failed with no success), REPLACE
                # the dressed-up prose with the honest floor. The pipeline floor band
                # (SP-1) remains the canonical replacer for pipeline callers; this
                # veto protects non-pipeline consumers (durable/A2A/parliament).
                if is_consequential_giveup_now():
                    floored = synthesize_from_calls(user_text, all_calls, text)
                    log.engine.info(
                        "[openai] complete_with_tools: wrap-up vetoed as give-up — flooring honest answer",
                        extra={"_fields": {"provider": self._name, "calls": len(all_calls)}},
                    )
                    return floored, all_calls
                log.engine.debug(
                    "[openai] complete_with_tools: wrap-up answer delivered at max-out",
                    extra={"_fields": {"provider": self._name, "len": len(text)}},
                )
                return text, all_calls
        except TurnStopped:
            # A user-stop / budget-kill is NOT a give-up — propagate, never floor it.
            raise
        except TimeoutError as exc:
            # F027 — the wrap-up exceeded its residual deadline. Route to the EXISTING
            # fail-open floor below (partial → synthesize_from_calls), bounded.
            log.engine.warning(
                "[openai] complete_with_tools: wrap-up exceeded deadline — flooring",
                exc_info=exc,
                extra={"_fields": {"provider": self._name, "deadline_s": wrapup_deadline_s}},
            )
        except Exception as exc:  # fail-open — never surface silence on a wrap-up error
            log.engine.error(
                "[openai] complete_with_tools: wrap-up call failed — falling back",
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
            "[openai] complete_with_tools: empty wrap-up — flooring honest answer",
            extra={"_fields": {"provider": self._name, "calls": len(all_calls)}},
        )
        floored = synthesize_from_calls(user_text, all_calls, partial)
        return floored, all_calls

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

    def _ollama_extra_body(self, resolved_model: str) -> dict[str, Any]:
        """For an ollama-family base_url, send the budgeted window as num_ctx so the
        server honors exactly the window we budgeted (else ollama truncates to its
        own default). Empty dict for non-ollama providers / unknown window."""
        base = self._config.base_url or ""
        if ":11434" not in base and "ollama" not in base.lower():
            return {}
        from stackowl.providers.model_window import cached_window
        w = cached_window(self._name, resolved_model)
        return {"extra_body": {"options": {"num_ctx": w}}} if w else {}

    def _complete_extra_body(self, resolved_model: str, *, disable_thinking: bool) -> dict[str, Any]:
        """Merge the ollama ``num_ctx`` window (if any) with an optional
        reasoning-disable hint into ONE ``extra_body`` kwarg for ``create()``.

        ``disable_thinking`` (passed by structured/classifier callers) tells a
        vLLM/Qwen-style reasoning endpoint to SKIP its ``<think>`` block for this
        one call via the standard OpenAI-compatible passthrough
        ``chat_template_kwargs={"enable_thinking": False}``. A 1-object JSON
        verdict needs no chain-of-thought; forcing it makes the model burn the
        whole token/timeout budget reasoning before it emits the answer the caller
        immediately discards (the live empty-verdict / 10s-timeout bug). Default
        ``False`` ⇒ byte-identical to prior behaviour; a provider whose endpoint
        ignores the hint is unaffected. Merges (not clobbers) the ollama window so
        both hints survive when a local reasoning model is also num_ctx-budgeted.
        """
        body: dict[str, Any] = {}
        ollama = self._ollama_extra_body(resolved_model)
        if ollama:
            body.update(ollama["extra_body"])
        if disable_thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        return {"extra_body": body} if body else {}

    def _output_cap(self, resolved_model: str) -> int:
        """Output-token budget for a generation — as much as the model's window
        allows, never a small fixed cap, but NEVER the whole window either.

        A fixed cap truncates a reasoning model mid-thought and starves the answer.
        The budget is bounded by the model's RESOLVED context window — a general
        abstraction that already works for every backend (no per-vendor logic) —
        but the window is a TOTAL (input + output) ceiling, not an output-only
        budget: requesting the entire window as ``max_tokens`` leaves zero room
        for the prompt itself and 400s on every real call once the window is a
        large, correctly-resolved value (live incident 2026-07-18 — NeraAiRaw's
        real 262144-token window used whole as max_tokens immediately exceeded
        the SAME window once the ~12k-token prompt was added on top). Bounding
        by ``max_output_tokens`` (a config ceiling already documented as
        "generous", not "small") keeps a genuinely small/unknown window
        unaffected (``min`` picks the window) while capping a huge window to a
        response size that safely coexists with real prompt sizes.
        """
        from stackowl.providers.model_window import cached_window

        window = cached_window(self._name, resolved_model)
        if window is None:
            return self._config.max_output_tokens
        return min(window, self._config.max_output_tokens)

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        TestModeGuard.assert_not_test_mode("openai.complete")
        log.engine.debug(
            "[openai] complete: entry",
            extra={"_fields": {"provider": self._name, "model": model}},
        )
        t0 = time.monotonic()
        resolved_model = model or self._config.default_model
        # ``disable_thinking`` (opt-in per call; classifier/structured callers set it)
        # routes to chat_template_kwargs enable_thinking=False so a reasoning model
        # emits its JSON verdict WITHOUT a preceding <think> block. Absent ⇒ unchanged.
        disable_thinking = bool(kwargs.get("disable_thinking", False))
        extra_body = self._complete_extra_body(resolved_model, disable_thinking=disable_thinking)
        # A message with image blocks → multimodal content parts (data-URL image);
        # a plain message keeps the cheap string form (B6 minimal change).
        oai_msgs = [
            {"role": m.role, "content": openai_user_content(m)}
            if message_has_blocks(m)
            else {"role": m.role, "content": m.content}
            for m in messages
        ]
        async def _round() -> Any:
            return await self._client.chat.completions.create(
                model=resolved_model,
                messages=oai_msgs,  # type: ignore[arg-type]
                max_tokens=_max_tokens(kwargs, default=self._output_cap(resolved_model)),
                **extra_body,
            )

        try:
            # F115 — record the per-round HTTP outcome onto the registry-owned breaker
            # the cascade reads; a classified APIError (wrapped below as ProviderError)
            # is the fault. resilient_round re-raises the original APIError unchanged.
            response = await self._resilient_round(_round)
        except openai.APIError as exc:
            log.engine.error(
                "[openai] complete: API error",
                exc_info=exc,
                extra={"_fields": {"provider": self._name}},
            )
            raise ProviderError(self._name, exc) from exc
        duration_ms = (time.monotonic() - t0) * 1000
        if not response.choices:
            raise ProviderError(self._name, ValueError("empty choices"))
        choice = response.choices[0]
        usage = response.usage
        # Keep ONLY the answer — discard the reasoning trace (thinking stays ON for
        # quality, but the <think> block is never used, stored, or returned).
        content = strip_think(choice.message.content or "")
        # Empty after stripping ⇒ a genuinely empty generation. Retry ONCE as a
        # cheap backstop. (With the output cap removed, mid-think truncation no
        # longer empties the content, so this rarely fires.)
        if not content:
            # F-22 — replaying the IDENTICAL round would just reproduce a
            # deterministic empty generation. VARY the retry: append a brief,
            # vendor-neutral continuation nudge so the prompt differs (and steer
            # the model away from a reasoning preamble that can eat the whole
            # budget — the documented live cause of an empty draft).
            retry_msgs = [
                *oai_msgs,
                {
                    "role": "user",
                    "content": (
                        "Your previous reply was empty. Please give your answer "
                        "directly now, without a reasoning preamble."
                    ),
                },
            ]
            log.engine.warning(
                "[openai] complete: empty after think-strip — retrying once with a varied prompt",
                extra={"_fields": {"provider": self._name, "model": resolved_model}},
            )

            async def _varied_round() -> Any:
                return await self._client.chat.completions.create(
                    model=resolved_model,
                    messages=retry_msgs,  # type: ignore[arg-type]
                    max_tokens=_max_tokens(kwargs, default=self._output_cap(resolved_model)),
                    **extra_body,
                )

            try:
                retry = await self._resilient_round(_varied_round)
            except openai.APIError as exc:
                log.engine.warning(
                    "[openai] complete: retry failed — keeping empty",
                    exc_info=exc,
                    extra={"_fields": {"provider": self._name}},
                )
                retry = None
            if retry is not None and retry.choices:
                choice = retry.choices[0]
                usage = retry.usage
                content = strip_think(choice.message.content or "")
            if not content:
                # Still empty after a varied retry — surface honestly rather than
                # passing "" off as a confident answer. The downstream give-up
                # floor turns this into an honest "couldn't produce a reply".
                log.engine.warning(
                    "[openai] complete: still empty after varied retry — "
                    "returning empty for the downstream floor",
                    extra={"_fields": {"provider": self._name, "model": resolved_model}},
                )
        result = CompletionResult(
            content=content,
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
