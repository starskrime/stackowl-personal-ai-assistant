"""AnthropicProvider — ModelProvider backed by the Anthropic Messages API."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal

import anthropic

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import ProviderError, TurnStopped
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.delivery_gate import is_consequential_giveup_now
from stackowl.pipeline.persistence import TOOL_FAILED_MARKER, summarize_tool_outcomes
from stackowl.pipeline.supervisor import decide_nudge, synthesize_from_calls
from stackowl.providers._blocks import anthropic_user_content, message_has_blocks
from stackowl.providers._cache_control import apply_cache_breakpoints
from stackowl.providers._react import LoopGuard, looks_like_tool_call, parse_react_action
from stackowl.providers._resilient_round import _is_transport_error
from stackowl.providers._truncate import (
    CONTEXT_CHAR_BUDGET,
    trim_messages_to_budget,
    truncate_observation,
)
from stackowl.providers._wrapup import FORMAT_FIX_DIRECTIVE, WRAPUP_DIRECTIVE
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.escalation_signal import escalation_requested
from stackowl.providers.llm_gateway import ESCALATE_SENTINEL
from stackowl.providers.model_config import resolve_model_override
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


# F027 extension — the in-loop tool round had NO app-level timeout (only the
# terminal wrap-up did), so a hung SDK call could stall a turn for however long
# the anthropic SDK's own default takes (minutes). Bounded by the SAME residual
# budget threaded in as wrapup_deadline_s when the caller (execute.py's
# BudgetGovernor) supplies one; this fallback covers a non-budgeted caller.
# Raised 120.0 -> 600.0 on 2026-07-22 to match authz/bounds.py's
# DEFAULT_TURN_MAX_TIME_S (fixing the same wall-clock-timeout-family inversion).
_ROUND_DEADLINE_FALLBACK_S = 600.0

# D01.2 — how many (incarnation, model) span measurements to keep before dropping
# the lot. Sessions roll over continuously, so without a bound this dict grows for
# the life of the process; clearing wholesale costs one re-measure per live
# session, which is cheaper than tracking recency for a cache this small.
_SPAN_CACHE_MAX = 256


def _cache_read_tokens(usage: Any) -> int:
    """Prompt-cache hits Anthropic reports, or 0 when it reports none (D01.2).

    A DIRECT read, deliberately — unlike the OpenAI-protocol sibling, which walks
    a three-shape priority chain because cache-stat naming is not standardised
    across gateways. Here the field is first-class in Anthropic's own API, so
    shape-probing would be dishonest: it would imply an uncertainty that does not
    exist on this protocol.

    The 0 is AMBIGUOUS by construction (D01.6's invariant I4): no cache hit, a
    cold cache, and a gateway that strips usage fields all read 0. It is never an
    error, and per I5 it is never persisted as evidence the markers are dead.

    NEVER raises — cost recording must not break a completion that already
    happened (B5).
    """
    if usage is None:
        return 0
    try:
        value = getattr(usage, "cache_read_input_tokens", None)
        return int(value) if value else 0
    except (TypeError, ValueError):
        return 0


def _cache_creation_tokens(usage: Any) -> int:
    """Tokens Anthropic reports WRITING to the cache this request (D01.2).

    This is the probe signal: a non-zero value is proof the endpoint honours
    ``cache_control`` markers at all. Never raises, for the same reason as above.
    """
    if usage is None:
        return 0
    try:
        value = getattr(usage, "cache_creation_input_tokens", None)
        return int(value) if value else 0
    except (TypeError, ValueError):
        return 0


class AnthropicProvider(ModelProvider):
    """Anthropic Messages API provider (claude-* family)."""

    def __init__(self, config: ProviderConfig, api_key: str) -> None:
        self._name = config.name
        self._config = config
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        # D01.2 — measured span sizes keyed by (session_id, model). See
        # _measure_spans for why the incarnation is the right cache scope.
        self._span_tokens: dict[tuple[str, str], dict[str, int]] = {}
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

    @property
    def supports_cache_breakpoints(self) -> bool:
        """D01.2 — Anthropic caches only what the request explicitly marks."""
        return True

    async def _measure_spans(
        self,
        *,
        model: str,
        tools: list[dict[str, Any]] | None,
        system: str | list[dict[str, Any]] | None,
    ) -> dict[str, int]:
        """Live token counts for the ``tools`` and ``system`` spans (D01.2).

        Measured ONCE PER SESSION and cached. D01.1 freezes the system prompt for
        the life of a conversation, so re-measuring every turn would spend a
        network round-trip to learn the same number — and the only question the
        number answers is "does this prefix clear the cacheable minimum", which
        cannot change while the prefix does not.

        TWO calls, not three. The tools span is measured alone; the tools+system
        prefix is measured together and the system span is the difference, so the
        constant overhead of the probe message CANCELS out of the system figure
        instead of needing its own baseline call.

        Returns ``{}`` on any failure — a gateway that does not implement
        ``count_tokens`` must still get markers, from the character estimate.
        The error is logged, never hidden.
        """
        # D01.7 — the incarnation is the natural cache scope, and it already rides
        # TraceContext. Background work with no session in context measures once
        # per model instead, which is the honest fallback rather than a fabricated
        # session id.
        session_id = str(TraceContext.get().get("session_id") or "")
        cache_key = (session_id, model)
        cached = self._span_tokens.get(cache_key)
        if cached is not None:
            return cached

        probe = [{"role": "user", "content": "."}]
        try:
            tools_count = 0
            if tools:
                tools_count = int(
                    (await self._client.messages.count_tokens(
                        model=model, messages=probe, tools=tools,  # type: ignore[arg-type]
                    )).input_tokens
                )
            prefix_count = tools_count
            if system is not None:
                kwargs: dict[str, Any] = {"model": model, "messages": probe, "system": system}
                if tools:
                    kwargs["tools"] = tools
                prefix_count = int(
                    (await self._client.messages.count_tokens(**kwargs)).input_tokens
                )
        except Exception as exc:
            # NOT hidden: a gateway without this endpoint is a normal deployment,
            # but a silent fallback would make the estimate's imprecision
            # untraceable at 2am.
            log.engine.error(
                "[cache] breakpoints: count_tokens unavailable — falling back to the char estimate",
                exc_info=exc,
                extra={"_fields": {"provider": self._name, "model": model}},
            )
            return {}

        measured = {"tools": tools_count, "system": max(prefix_count - tools_count, 0)}
        # Bounded: one entry per (incarnation, model). Sessions roll over, and an
        # unbounded dict on a long-lived provider object is a slow leak.
        if len(self._span_tokens) >= _SPAN_CACHE_MAX:
            self._span_tokens.clear()
        self._span_tokens[cache_key] = measured
        log.engine.debug(
            "[cache] breakpoints: spans measured",
            extra={"_fields": {"provider": self._name, "model": model,
                               "session_id": session_id, **measured}},
        )
        return measured

    async def _request_kwargs(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
        system: str | list[dict[str, Any]] | None = None,
        capable: bool | None = None,
    ) -> dict[str, Any]:
        """THE chokepoint — every Messages API request this provider sends is built here.

        All three ``messages.create()`` sites AND the ``messages.stream()`` site
        route through this one method, so marking happens in exactly one place and
        a call site added later inherits it rather than silently skipping it. That
        is the divergence from the reference platform, which marks in three files
        that cannot see each other and therefore cannot prove the four-marker
        budget is respected.

        ``capable`` overrides the capability gate for tests; production passes
        ``None`` and the provider's own declaration decides.

        Invariant I3: if the marker layer raises, the error is LOGGED and the
        request goes out UNMARKED. Caching is an optimisation, never a dependency.
        """
        kwargs: dict[str, Any] = {
            "model": model, "messages": messages, "max_tokens": max_tokens,
        }
        if tools is not None:
            kwargs["tools"] = tools
        if system is not None:
            kwargs["system"] = system

        if not (self.supports_cache_breakpoints if capable is None else capable):
            return kwargs

        try:
            measured = await self._measure_spans(model=model, tools=tools, system=system)
            marked_tools, marked_system, marked_messages = apply_cache_breakpoints(
                tools, system, messages,
                model=model,
                ttl=self._config.cache_ttl,
                measured_tokens=measured or None,
            )
        except Exception as exc:  # I3 — never let marking cost a turn.
            log.engine.error(
                "[cache] breakpoints: marking FAILED — sending the request unmarked",
                exc_info=exc,
                extra={"_fields": {"provider": self._name, "model": model}},
            )
            return kwargs

        kwargs["messages"] = marked_messages
        if tools is not None:
            kwargs["tools"] = marked_tools
        if system is not None:
            kwargs["system"] = marked_system
        return kwargs

    async def stream(self, messages: list[Message], model: str, **kwargs: object) -> AsyncIterator[str]:
        TestModeGuard.assert_not_test_mode("anthropic.stream")
        log.engine.debug(
            "[anthropic] stream: entry",
            extra={"_fields": {"provider": self._name, "model": model, "msg_count": len(messages)}},
        )
        system_parts = [m.content for m in messages if m.role == "system"]
        chat_msgs = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
        resolved_model = model or self._config.default_model
        # A typed local, not a kwargs dict: the dict existed only to be splatted
        # into stream(), and now that the request is built at the chokepoint the
        # untyped `object` value was the one thing standing between this file and
        # a clean mypy run.
        stream_system: str | None = "\n\n".join(system_parts) if system_parts else None
        _t0 = time.monotonic()
        # The stream site routes through the SAME chokepoint as the three create()
        # sites — messages.stream() is a context-manager factory rather than a
        # coroutine, so it cannot share a create() wrapper, but it shares the one
        # place where markers are decided. That is what makes the four-marker
        # budget provable instead of enforced in four places that cannot see each
        # other.
        _stream_request = await self._request_kwargs(
            model=resolved_model,
            messages=chat_msgs,
            # NOTE: max_output_tokens' 250000 default exceeds real Anthropic
            # per-model ceilings (8192-64000) — safe today (no Anthropic
            # provider configured), but the FIRST Anthropic provider added
            # must set an explicit models[].max_output_tokens (or a smaller
            # provider-level max_output_tokens) or its first real request
            # fails with a 400. No window-bounding exists for this
            # provider (unlike OpenAI's _output_cap) — deliberately out of
            # scope for this plan.
            max_tokens=_max_tokens(
                kwargs, default=resolve_model_override(self._config, resolved_model)[0]
            ),
            system=stream_system,
        )
        try:
            async with self._client.messages.stream(**_stream_request) as stream:
                try:
                    async for text in stream.text_stream:
                        yield text
                finally:
                    # F119 — record the streamed round's spend at generator exit
                    # (even on early abandon). get_final_message() carries the usage;
                    # best-effort, fail-open (B5) — never break the conversational path.
                    await self._record_stream_usage_safe(
                        stream, resolved_model, (time.monotonic() - _t0) * 1000
                    )
        except Exception as exc:
            # F-21 — wrap the BROADER transport set (raw ConnectionError/TimeoutError,
            # a non-429 SDK error, a 5xx/429) as ProviderError so the gateway sees a
            # uniform fault, matching the Gemini sibling. A non-transport exception
            # (a routing control signal like CircuitOpenError/RateLimitError, a
            # user-stop, or our own bug) propagates UNWRAPPED so the cascade can
            # still classify it.
            if not _is_transport_error(exc):
                raise
            log.engine.error(
                "[anthropic] stream: transport error",
                exc_info=exc,
                extra={"_fields": {"provider": self._name}},
            )
            raise ProviderError(self._name, exc) from exc
        log.engine.debug("[anthropic] stream: exit", extra={"_fields": {"provider": self._name}})

    async def _record_stream_usage_safe(
        self, stream: Any, model: str, duration_ms: float
    ) -> None:
        """Record one streamed round's cost from the final message usage (F119).

        Fail-open (B5): ``get_final_message()`` / usage extraction may raise on an
        abandoned or fake stream — record nothing, log at DEBUG, never break the
        streamed reply.
        """
        try:
            final = await stream.get_final_message()
            usage = getattr(final, "usage", None)
            if usage is None:
                log.engine.debug(
                    "[anthropic] stream: no usage on final message — recording nothing",
                    extra={"_fields": {"provider": self._name}},
                )
                return
            in_tok = int(getattr(usage, "input_tokens", 0) or 0)
            out_tok = int(getattr(usage, "output_tokens", 0) or 0)
            rec_model = getattr(final, "model", model) or model
            # D01.2 — base.py::_record_cost has accepted this since D01.6 and the
            # Anthropic path has fed it 0 every time. This is the reader half.
            cached_tok = _cache_read_tokens(usage)
        except Exception as exc:  # B5 — never break the stream on cost accounting.
            log.engine.debug(
                "[anthropic] stream: usage extraction skipped",
                extra={"_fields": {"provider": self._name, "err": str(exc)}},
            )
            return
        await self._record_cost(
            model=rec_model, input_tokens=in_tok, output_tokens=out_tok, duration_ms=duration_ms,
            cached_input_tokens=cached_tok,
        )

    async def complete_with_tools(
        self,
        user_text: str,
        system_text: str | None,
        tool_schemas: list[dict[str, Any]],
        tool_dispatcher: Callable[[str, dict[str, Any]], Awaitable[str]],
        model: str = "",
        max_iterations: int = 8,
        history: list[Message] | None = None,
        persistence_check: Callable[[str, list[str]], Awaitable[str | None]] | None = None,
        on_iteration_complete: IterationCallback | None = None,
        resume_messages: list[dict[str, Any]] | None = None,
        resume_tool_calls: list[dict[str, Any]] | None = None,
        wrapup_deadline_s: float | None = None,
        can_escalate: bool = False,
        max_tokens: int | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Anthropic native tool-use loop using content blocks.

        ``max_tokens`` (live incident 2026-07-22): explicit per-call override
        for the output-token budget, mirroring the OpenAI-compatible provider's
        own ``max_tokens`` param. ``None`` (default) preserves today's
        behavior: every round uses ``self._config.max_output_tokens``
        unconditionally.

        ``can_escalate`` (set ONLY by LLMGateway below the ceiling tier) mirrors the
        openai provider: when the model persistently emits an unparseable tool call
        as TEXT (a leak) this loop returns the ESCALATE sentinel so the gateway
        re-runs on a stronger tier — instead of leaking the raw tool-call text or
        flooring. Default False ⇒ unchanged behaviour (honest floor).

        ``model`` (Task 22): resolved per-model override for THIS call — same
        ``model or self._config.default_model`` fallback as ``complete()``/
        ``stream()``. Unlike the OpenAI sibling this loop has TWO independent
        API call sites (the in-loop tool round and the terminal wrap-up round),
        each of which previously hardcoded ``self._config.default_model`` on
        its own — both are resolved from the SAME ``resolved_model`` local so
        neither can drift from the other."""
        TestModeGuard.assert_not_test_mode("anthropic.complete_with_tools")
        resolved_model = model or self._config.default_model
        resolved_iterations = max_iterations if max_iterations != 8 else self._config.tool_max_iterations
        log.engine.debug(
            "[anthropic] complete_with_tools: entry",
            extra={"_fields": {
                "provider": self._name,
                "model": resolved_model,
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
                nudges_issued=nudges_issued,
            )
            if directive:
                nudges_issued += 1
                log.engine.info(
                    "[anthropic] complete_with_tools: persistence nudge — continuing loop",
                    extra={
                        "_fields": {
                            "provider": self._name,
                            "nudge_budget": nudge_budget,
                            "nudges_issued": nudges_issued,
                        }
                    },
                )
            return directive

        # Full resolved context_chars, no artificial 80% shrink (owner decision
        # 2026-07-22) — CONTEXT_CHAR_BUDGET only backstops the case where
        # context_chars is entirely unconfigured, not a normal-path ceiling.
        budget = self._config.context_chars or CONTEXT_CHAR_BUDGET

        guard = LoopGuard()
        # Known tool names (anthropic schema shape: {"name": ...}) — lets the ReAct
        # text parser validate/repair a flattened-newline name and reject a bogus one.
        _known_tools = {
            s["name"]
            for s in tool_schemas
            if isinstance(s, dict) and isinstance(s.get("name"), str)
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
                    "[anthropic] complete_with_tools: circuit-open this turn — escalating to a stronger tier",
                    extra={"_fields": {"provider": self._name}},
                )
                return ESCALATE_SENTINEL, all_calls
            # Bound total context BEFORE the call. Only tool_result CONTENT is
            # elided (never the message itself), so tool_use/tool_result pairing
            # stays valid for the Anthropic API.
            messages = trim_messages_to_budget(messages, budget)
            _t_call = time.monotonic()

            async def _round(_msgs: list[dict[str, Any]] = messages) -> Any:
                return await self._client.messages.create(
                    **await self._request_kwargs(
                        model=resolved_model,
                        messages=_msgs,
                        max_tokens=(
                            max_tokens if max_tokens is not None
                            else self._config.max_output_tokens
                        ),
                        tools=tool_schemas,
                        system=system_kwargs.get("system"),
                    )
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
                except anthropic.APIError as exc:
                    log.engine.error(
                        "[anthropic] complete_with_tools: API error",
                        exc_info=exc,
                        extra={"_fields": {
                            "provider": self._name,
                            "duration_ms": (time.monotonic() - _t_call) * 1000,
                        }},
                    )
                    raise ProviderError(self._name, exc) from exc
                except TimeoutError as exc:
                    log.engine.error(
                        "[anthropic] complete_with_tools: round exceeded deadline — hung provider call",
                        exc_info=exc,
                        extra={"_fields": {
                            "provider": self._name,
                            "duration_ms": (time.monotonic() - _t_call) * 1000,
                        }},
                    )
                    raise ProviderError(self._name, exc) from exc

                round_duration_ms = (time.monotonic() - _t_call) * 1000
                log.engine.debug(
                    "[anthropic] complete_with_tools: round ok",
                    extra={"_fields": {
                        "provider": self._name, "iteration": _iter_idx,
                        "duration_ms": round_duration_ms,
                    }},
                )

            # E8-S0cost — record EACH internal tool-loop API round's spend (B5:
            # usage extraction is itself fail-open — a response shape without usage
            # must never break the loop).
            await self._record_usage_safe(response, round_duration_ms)

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
                # Text-protocol parity with openai: a model that emitted a tool call
                # as TEXT (an ACTION block) instead of a native tool_use block is
                # dispatched through the normal chokepoint rather than delivered raw.
                action = parse_react_action(text, known=_known_tools)
                if action is not None:
                    name, args = action
                    messages.append({"role": "assistant", "content": text})
                    try:
                        result_text = await tool_dispatcher(name, args)
                    except Exception as exc:  # surface to the model, keep looping
                        log.engine.error(
                            "[anthropic] react dispatch failed",
                            exc_info=exc,
                            extra={"_fields": {"provider": self._name, "tool": name}},
                        )
                        result_text = f"ERROR running {name}: {exc}"
                    # Strip the private failure marker before the model/telemetry see it.
                    clean = result_text.replace(TOOL_FAILED_MARKER, "")
                    capped = truncate_observation(clean)
                    failed = TOOL_FAILED_MARKER in result_text
                    all_calls.append({"id": None, "name": name, "args": args, "result": capped, "failed": failed})
                    react_directive = guard.observe(name, args)
                    messages.append({"role": "user", "content": f"OBSERVATION: {capped}"})
                    if react_directive:
                        messages.append({"role": "user", "content": react_directive})
                    if on_iteration_complete is not None:
                        folded2 = await on_iteration_complete(
                            ReActIterationState(
                                iteration=_iter_idx,
                                messages=list(messages),
                                tool_call_records=list(all_calls),
                            )
                        )
                        if folded2:
                            messages.extend(folded2)
                    if guard.tripped():
                        log.engine.warning(
                            "[anthropic] complete_with_tools: loop guard tripped "
                            "(ReAct text path) — breaking to wrap-up",
                            extra={"_fields": {"provider": self._name}},
                        )
                        break
                    continue
                # LEAK GUARD: the "final answer" is actually an unparsed tool call
                # (an ACTION block / bare JSON we couldn't dispatch). NEVER deliver
                # that raw text. Re-prompt the exact format a bounded number of times;
                # if it persists, ESCALATE to a stronger tier when allowed (the gateway
                # re-runs), else fall to the honest floor — never the raw tool call.
                if looks_like_tool_call(text, known=_known_tools):
                    if _fmt_fix_count < _MAX_FORMAT_FIX:
                        _fmt_fix_count += 1
                        log.engine.warning(
                            "[anthropic] complete_with_tools: final answer looks like an "
                            "unparsed tool call — re-prompting the ACTION format",
                            extra={"_fields": {"provider": self._name, "attempt": _fmt_fix_count}},
                        )
                        messages.append({"role": "user", "content": FORMAT_FIX_DIRECTIVE})
                        continue
                    if can_escalate:
                        log.engine.warning(
                            "[anthropic] complete_with_tools: persistent tool-call leak — "
                            "escalating to a stronger tier",
                            extra={"_fields": {"provider": self._name}},
                        )
                        return ESCALATE_SENTINEL, all_calls
                    log.engine.warning(
                        "[anthropic] complete_with_tools: persistent tool-call leak, no "
                        "escalation available — honest floor instead of raw tool call",
                        extra={"_fields": {"provider": self._name}},
                    )
                    return synthesize_from_calls(user_text, all_calls, ""), all_calls
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

            async def _wrap_round() -> Any:
                return await self._client.messages.create(
                    **await self._request_kwargs(
                        model=resolved_model,
                        messages=messages,
                        max_tokens=(
                            max_tokens if max_tokens is not None
                            else self._config.max_output_tokens
                        ),
                        system=system_kwargs.get("system"),
                    )
                )

            # F027 — bound the terminal wrap-up by the governor's residual budget.
            # None → byte-identical to today (no wait_for). Wall-clock wait_for around
            # the await (a post-hoc check cannot bound a hang).
            if wrapup_deadline_s is not None:
                wrapup = await asyncio.wait_for(
                    self._resilient_round(_wrap_round), timeout=wrapup_deadline_s,
                )
            else:
                wrapup = await self._resilient_round(_wrap_round)
            await self._record_usage_safe(wrapup, (time.monotonic() - _t_wrap) * 1000)
            text = "".join(b.text for b in wrapup.content if hasattr(b, "text"))
            if text.strip():
                # F026 — same structural give-up veto as the in-loop boundary, applied
                # to the terminal wrap-up. Pure predicate (no nudge: no loop to
                # continue; no LLM judge: no extra network call). On a dishonest
                # give-up replace the dressed-up prose with the honest floor. SP-1's
                # pipeline floor band stays the canonical replacer for pipeline
                # callers; this protects non-pipeline consumers.
                if is_consequential_giveup_now():
                    floored = synthesize_from_calls(user_text, all_calls, text)
                    log.engine.info(
                        "[anthropic] complete_with_tools: wrap-up vetoed as give-up — flooring honest answer",
                        extra={"_fields": {"provider": self._name, "calls": len(all_calls)}},
                    )
                    return floored, all_calls
                log.engine.debug(
                    "[anthropic] complete_with_tools: wrap-up answer delivered at max-out",
                    extra={"_fields": {"provider": self._name, "len": len(text)}},
                )
                return text, all_calls
        except TurnStopped:
            # A user-stop / budget-kill is NOT a give-up — propagate, never floor it.
            raise
        except TimeoutError as exc:
            # F027 — wrap-up exceeded its residual deadline. Route to the EXISTING
            # fail-open floor below (partial → synthesize_from_calls), bounded.
            log.engine.warning(
                "[anthropic] complete_with_tools: wrap-up exceeded deadline — flooring",
                exc_info=exc,
                extra={"_fields": {"provider": self._name, "deadline_s": wrapup_deadline_s}},
            )
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
            # D01.2 — the reader half. See _cache_read_tokens for why a 0 here is
            # ambiguous rather than proof the markers are dead.
            cached_tok = _cache_read_tokens(usage)
            created_tok = _cache_creation_tokens(usage)
        except Exception as exc:  # B5 — usage shape varies / may be absent on fakes.
            log.engine.debug(
                "[anthropic] _record_usage_safe: usage extraction skipped",
                extra={"_fields": {"provider": self._name, "err": str(exc)}},
            )
            return
        # INFO, not debug, and deliberately so. D01.6 learned this the hard way:
        # the per-part prompt sizes it needed were logged at debug and appeared in
        # 0 of 17403 live log lines, which is why prompt composition was
        # unmeasurable for days. This line is how "is caching actually working?"
        # gets answered from the JSONL instead of guessed.
        log.engine.info(
            "[cache] breakpoints: result",
            extra={"_fields": {"provider": self._name, "model": model,
                               "cache_read": cached_tok, "cache_creation": created_tok,
                               "input_tokens": in_tok}},
        )
        await self._record_cost(
            model=model, input_tokens=in_tok, output_tokens=out_tok, duration_ms=duration_ms,
            cached_input_tokens=cached_tok,
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
        sys_kwargs: dict[str, Any] = (
            {"system": "\n\n".join(system_parts)} if system_parts else {}
        )

        async def _round() -> Any:
            return await self._client.messages.create(
                **await self._request_kwargs(
                    model=resolved_model,
                    messages=chat_msgs,
                    # NOTE: max_output_tokens' 250000 default exceeds real Anthropic
                    # per-model ceilings (8192-64000) — safe today (no Anthropic
                    # provider configured), but the FIRST Anthropic provider added
                    # must set an explicit models[].max_output_tokens (or a smaller
                    # provider-level max_output_tokens) or its first real request
                    # fails with a 400. No window-bounding exists for this
                    # provider (unlike OpenAI's _output_cap) — deliberately out of
                    # scope for this plan.
                    max_tokens=_max_tokens(
                        kwargs, default=resolve_model_override(self._config, resolved_model)[0]
                    ),
                    system=sys_kwargs.get("system"),
                )
            )

        try:
            # F115 — record the per-round HTTP outcome onto the registry-owned breaker.
            response = await self._resilient_round(_round)
        except Exception as exc:
            # F-21 — wrap the broader transport set (raw connection/timeout, SDK
            # error, 5xx/429) consistently; a control signal / our-own bug propagates
            # unwrapped so the gateway cascade classifies it correctly.
            if not _is_transport_error(exc):
                raise
            log.engine.error(
                "[anthropic] complete: transport error",
                exc_info=exc,
                extra={"_fields": {"provider": self._name}},
            )
            raise ProviderError(self._name, exc) from exc
        duration_ms = (time.monotonic() - t0) * 1000
        content = "".join(b.text for b in response.content if hasattr(b, "text"))
        # F-20 — an empty/whitespace generation is not an honest success. Retry the
        # round ONCE (parity with the OpenAI sibling); if the retry produces real
        # text, use it. A still-empty result is returned with a warning logged (not
        # silently) so the downstream honesty floor — not a confident-looking empty
        # success — handles it.
        if not content.strip():
            log.engine.warning(
                "[anthropic] complete: empty content — retrying once",
                extra={"_fields": {"provider": self._name, "model": resolved_model}},
            )
            try:
                retry = await self._resilient_round(_round)
            except Exception as exc:
                if not _is_transport_error(exc):
                    raise
                log.engine.warning(
                    "[anthropic] complete: retry after empty failed — keeping empty",
                    exc_info=exc,
                    extra={"_fields": {"provider": self._name}},
                )
            else:
                response = retry
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
