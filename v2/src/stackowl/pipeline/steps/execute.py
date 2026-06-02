"""Pipeline step 4: execute — stream from ModelProvider through OwlResourceGuard."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

from stackowl.exceptions import (
    OwlConcurrencyError,
    OwlTimeoutError,
    OwlTokenLimitError,
)
from stackowl.infra.observability import log
from stackowl.owls.guards import OwlResourceGuard
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState, ToolCall
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.providers.base import Message, ModelProvider
from stackowl.tools.registry import ToolRegistry

# E8-S0 — tools a delegated child (delegation_depth>0) must NOT see, so a child
# cannot recurse into a fork-bomb. Names are matched defensively (the tools are
# registered by later stories S1/S3); excluding by name is correct ahead of them.
# E9-S2/FF-E9-5 — `process` joins them: a child handles its sub-task and returns
# without leaving persistent OS processes running past the parent turn (the S0
# count-cap + mandatory TTL still bound the top-level owl).
_CHILD_EXCLUDED_TOOLS = frozenset(
    {"delegate_task", "sessions_spawn", "sessions_send", "process"}
)


def _schema_tool_name(schema: dict[str, object]) -> str:
    """Extract the tool name from a provider schema (anthropic or openai shape)."""
    name = schema.get("name")
    if isinstance(name, str):  # anthropic protocol shape
        return name
    fn = schema.get("function")
    if isinstance(fn, dict):  # openai protocol shape: {"function": {"name": ...}}
        inner = fn.get("name")
        if isinstance(inner, str):
            return inner
    return ""


def _exclude_spawn_tools(schemas: list[dict[str, object]]) -> list[dict[str, object]]:
    """Drop spawn/delegate tools from a presented schema list (depth>0 children)."""
    return [s for s in schemas if _schema_tool_name(s) not in _CHILD_EXCLUDED_TOOLS]


async def _run_with_tools(
    state: PipelineState,
    provider: ModelProvider,
    tool_registry: ToolRegistry,
) -> PipelineState:
    """Execute the provider's tool loop and return updated state."""
    # E1-S4 — DNA-gated presented set: an owl with a non-empty capability_profile
    # sees base ∪ its groups ∪ pins ∪ tool_search (capped); overflow via tool_search.
    # Owls without a profile keep the full catalog (no regression).
    #
    # NOTE: gating is PRESENTATION, not authorization. _dispatch (below) resolves
    # tools from the FULL registry, so a tool_search'd overflow tool stays callable
    # by name even when it is not in this turn's schema — that is how overflow stays
    # reachable. The consent gate (not gating) is the real access-control boundary.
    profile: list[str] | None = None
    pins: list[str] | None = None
    owl_registry = get_services().owl_registry
    if owl_registry is not None:
        try:
            owl_manifest = owl_registry.get(state.owl_name)
            if owl_manifest.capability_profile:
                profile = list(owl_manifest.capability_profile)
                pins = list(owl_manifest.tools)
        except Exception as exc:  # unknown owl / lookup failure → no gating (safe)
            log.engine.debug(
                "[pipeline] execute: owl profile lookup failed — full catalog",
                exc_info=exc, extra={"_fields": {"owl": state.owl_name}},
            )
    tool_schemas = tool_registry.to_provider_schema(provider.protocol, profile=profile, pins=pins)
    # E8-S0 — child-toolset exclusion (PRIMARY fork-bomb cap): a delegated child
    # (delegation_depth>0) may not itself spawn/delegate, so remove those two
    # tools from the PRESENTED set. Excluded by NAME defensively so it is correct
    # once S1/S3 register delegate_task/sessions_spawn (they don't exist yet).
    if state.delegation_depth > 0:
        tool_schemas = _exclude_spawn_tools(tool_schemas)
        log.engine.debug(
            "[pipeline] execute: depth>0 — excluding spawn/delegate tools",
            extra={"_fields": {
                "trace_id": state.trace_id,
                "delegation_depth": state.delegation_depth,
                "tools": len(tool_schemas),
            }},
        )
    log.engine.info(
        "[pipeline] execute: tool_loop entry",
        extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name, "tools": len(tool_schemas)}},
    )

    # F3.1 — within a single run, a tool denied once must not re-prompt the user
    # if the model stubbornly re-calls it; short-circuit subsequent calls.
    denied_this_run: set[str] = set()

    async def _dispatch(name: str, args: dict[str, object]) -> str:
        # E8-S0 — EXECUTION-layer fork-bomb cap (not just presentation). A delegated
        # child (delegation_depth>0) is refused these tools even if it names one the
        # presented schema omitted: presentation gating is not authorization, so the
        # depth gate must enforce HERE, fail-closed, from the TRUSTED state.
        if state.delegation_depth > 0 and name in _CHILD_EXCLUDED_TOOLS:
            log.engine.warning(
                "[pipeline] execute: depth>0 child denied spawn/delegate tool",
                extra={"_fields": {"tool": name, "trace_id": state.trace_id,
                                   "delegation_depth": state.delegation_depth}},
            )
            return (
                f"'{name}' is not available to a delegated sub-agent (delegation depth "
                f"limit reached). Complete the task yourself and return your result."
            )
        t = tool_registry.get(name)
        if t is None:
            log.engine.warning("[pipeline] execute: unknown tool in dispatch", extra={"_fields": {"tool": name}})
            return f"Tool not found: {name}"
        if name in denied_this_run:
            log.engine.info(
                "[pipeline] execute: tool already declined this run — not re-prompting",
                extra={"_fields": {"tool": name, "trace_id": state.trace_id}},
            )
            return (
                f"The action '{name}' was already declined this turn. Do not call it again — "
                "respond to the user instead."
            )
        # E0-S1 — consent gate runs BEFORE execution for consequential tools.
        # The category is derived inside gate.check() from the TRUSTED manifest,
        # never from LLM-supplied args. Fail closed: a gate error, OR a missing
        # gate on a consequential tool, denies rather than runs it.
        gate = get_services().consent_gate
        is_consequential = t.manifest.action_severity == "consequential"
        if gate is not None:
            try:
                allowed = await gate.check(t, channel=state.channel, session_id=state.session_id)
            except Exception as exc:
                log.engine.error(
                    "[pipeline] execute: consent gate raised — denying (fail closed)",
                    exc_info=exc,
                    extra={"_fields": {"tool": name, "trace_id": state.trace_id}},
                )
                allowed = False
        elif is_consequential:
            # No gate wired but the tool is consequential → fail closed (never run
            # a consequential action without a functioning consent control).
            log.engine.error(
                "[pipeline] execute: consequential tool but NO consent gate wired — denying",
                extra={"_fields": {"tool": name, "trace_id": state.trace_id}},
            )
            allowed = False
        else:
            allowed = True
        if not allowed:
            denied_this_run.add(name)
            log.engine.info(
                "[pipeline] execute: consequential action declined by gate",
                extra={"_fields": {"tool": name, "trace_id": state.trace_id, "session_id": state.session_id}},
            )
            return (
                f"The action '{name}' requires your approval and was not run because consent "
                "was declined or not granted. Ask the user to approve it if they want it to proceed."
            )
        tr = await t(**args)
        # Learning Commit 5 — post-execute heuristic match + event emission.
        # Zero behavior change; downstream subscribers (classify, future hooks)
        # see "tool.heuristic_match" when a known-bad pattern fires.
        services = get_services()
        if services.heuristic_store is not None and services.event_bus is not None:
            from stackowl.learning.heuristic_matcher import match_and_emit

            try:
                await match_and_emit(
                    tool_name=name, tool_result=tr,
                    heuristic_store=services.heuristic_store,
                    event_bus=services.event_bus,
                )
            except Exception as exc:  # B5 — never block dispatch on a telemetry hook
                log.engine.warning(
                    "[pipeline] execute: heuristic match failed — continuing",
                    exc_info=exc,
                    extra={"_fields": {"tool": name}},
                )
        if tr.success:
            return tr.output
        # FAILED — prefix the rendered error with the structural marker so the
        # give-up judge (which sees only these rendered strings) can tell a failed
        # action from a successful one. Language-agnostic; the model still reads a
        # normal error message after the (invisible-ish) sentinel.
        from stackowl.pipeline.persistence import TOOL_FAILED_MARKER

        return f"{TOOL_FAILED_MARKER}{tr.error or tr.output}"

    # Phase D — real-time persistence enforcer. Build a deliver-vs-giveup callback
    # the provider loop calls just before accepting a final answer. The provider
    # cannot reach the provider_registry; execute (which has services) can — so we
    # close over it here. GATING: only interactive user turns at delegation depth 0
    # get enforced, so cron/parliament/delegated sub-pipelines are never nudged.
    persistence_check = None
    if state.interactive and state.delegation_depth == 0:
        from stackowl.pipeline.persistence import (
            PERSISTENCE_DIRECTIVE,
            judge_delivery,
        )

        async def _persistence_check(draft: str, tools_tried: list[str]) -> str | None:
            """Judge the draft answer; return the corrective directive on give-up.

            Fail-OPEN: any error (no judge provider, judge raises) returns None so
            the answer is accepted and the turn never hangs/loops.
            """
            try:
                preg = get_services().provider_registry
                if preg is None:  # no registry → cannot judge; accept (fail open)
                    return None
                judge_provider = preg.get_with_cascade("fast")
                delivered, reason = await judge_delivery(
                    judge_provider, state.input_text, draft, tools_tried
                )
            except Exception as exc:  # fail OPEN — never block the turn
                log.engine.error(
                    "[pipeline] execute: persistence judge failed — accepting answer",
                    exc_info=exc,
                    extra={"_fields": {"trace_id": state.trace_id}},
                )
                return None
            if not delivered:
                log.engine.info(
                    "[pipeline] execute: persistence judge ruled give-up — nudging",
                    extra={"_fields": {"trace_id": state.trace_id, "reason": reason[:120]}},
                )
                return PERSISTENCE_DIRECTIVE
            return None

        persistence_check = _persistence_check

    t0 = time.monotonic()
    # Only forward persistence_check when it is actually enabled (interactive,
    # depth 0). Omitting the kwarg otherwise keeps the call backward-compatible
    # with every provider implementation (no new kwarg on the non-interactive path).
    try:
        if persistence_check is not None:
            final_text, raw_calls = await provider.complete_with_tools(
                user_text=state.input_text,
                system_text=state.system_prompt,
                tool_schemas=tool_schemas,
                tool_dispatcher=_dispatch,
                history=list(state.history),
                persistence_check=persistence_check,
            )
        else:
            final_text, raw_calls = await provider.complete_with_tools(
                user_text=state.input_text,
                system_text=state.system_prompt,
                tool_schemas=tool_schemas,
                tool_dispatcher=_dispatch,
                history=list(state.history),
            )
    except Exception as exc:
        log.engine.error(
            "[pipeline] execute: tool_loop failed",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
        )
        return state.evolve(errors=(*state.errors, f"execute: {type(exc).__name__}: {exc}"))

    duration_ms = (time.monotonic() - t0) * 1000
    tool_records = tuple(
        ToolCall(
            tool_name=str(rc.get("name", "")),
            args=dict(rc.get("args") or {}),
            result=str(rc.get("result", "")),
            error=None,
            duration_ms=0.0,
        )
        for rc in raw_calls
    )
    chunks: tuple[ResponseChunk, ...] = ()
    if final_text:
        chunks = (ResponseChunk(
            content=final_text,
            is_final=False,
            chunk_index=0,
            trace_id=state.trace_id,
            owl_name=state.owl_name,
        ),)
    log.engine.info(
        "[pipeline] execute: tool_loop exit",
        extra={"_fields": {
            "trace_id": state.trace_id,
            "owl": state.owl_name,
            "tool_calls": len(raw_calls),
            "duration_ms": duration_ms,
        }},
    )
    return state.evolve(
        responses=(*state.responses, *chunks),
        tool_calls=(*state.tool_calls, *tool_records),
    )


def _resolve_manifest(owl_name: str) -> OwlAgentManifest | None:
    """Best-effort lookup of an owl manifest; returns None on any miss."""
    services = get_services()
    registry = services.owl_registry
    if registry is None:
        log.engine.debug(
            "[pipeline] execute: no owl_registry — guard disabled",
            extra={"_fields": {"owl": owl_name}},
        )
        return None
    try:
        return registry.get(owl_name)
    except Exception as exc:
        log.engine.warning(
            "[pipeline] execute: owl manifest lookup failed — guard disabled",
            exc_info=exc,
            extra={"_fields": {"owl": owl_name}},
        )
        return None


def _open_stream(
    provider: ModelProvider,
    manifest: OwlAgentManifest | None,
    messages: list[Message],
) -> AsyncIterator[str]:
    """Return a guarded stream when a manifest exists, else a raw provider stream."""
    if manifest is None:
        return provider.stream(messages, model="")
    guard = OwlResourceGuard(manifest)
    return guard.stream(provider, messages, model="")


async def run(state: PipelineState) -> PipelineState:
    """Stream tokens from the assigned provider and build state.responses."""
    log.engine.info(
        "[pipeline] execute: entry",
        extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
    )
    services = get_services()
    registry = services.provider_registry
    tool_registry = services.tool_registry
    if registry is None:
        log.engine.warning("[pipeline] execute: no provider_registry — pass-through")
        return state

    try:
        provider = registry.get(state.owl_name)
    except Exception as exc:
        owl_registry = services.owl_registry
        known_owl = False
        if owl_registry is not None:
            try:
                owl_registry.get(state.owl_name)
                known_owl = True
            except Exception:
                known_owl = False
        if known_owl:
            log.engine.info(
                "[pipeline] execute: no per-owl provider — tier-routing to 'powerful'",
                extra={"_fields": {"owl": state.owl_name}},
            )
        else:
            log.engine.warning(
                "[pipeline] execute: unknown owl_name — falling back to 'powerful' tier",
                exc_info=exc,
                extra={"_fields": {"owl": state.owl_name}},
            )
        provider = registry.get_by_tier("powerful")

    # Tool loop path: use complete_with_tools() when tools are available
    if tool_registry is not None and tool_registry.all():
        return await _run_with_tools(state, provider, tool_registry)

    messages: list[Message] = [*state.history, Message(role="user", content=state.input_text)]
    if state.system_prompt:
        messages = [Message(role="system", content=state.system_prompt), *messages]

    manifest = _resolve_manifest(state.owl_name)
    stream_iter = _open_stream(provider, manifest, messages)

    t0 = time.monotonic()
    chunks: list[ResponseChunk] = []
    chunk_index = 0
    try:
        async for text in stream_iter:
            chunk = ResponseChunk(
                content=text,
                is_final=False,
                chunk_index=chunk_index,
                trace_id=state.trace_id,
                owl_name=state.owl_name,
            )
            chunks.append(chunk)
            chunk_index += 1
    except OwlTimeoutError as exc:
        log.engine.warning(
            "[pipeline] execute: owl timeout",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
        )
        return state.evolve(
            responses=(*state.responses, *chunks),
            errors=(*state.errors, f"execute: OwlTimeoutError: {exc}"),
        )
    except OwlConcurrencyError as exc:
        log.engine.warning(
            "[pipeline] execute: owl concurrency limit",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
        )
        return state.evolve(
            errors=(*state.errors, f"execute: OwlConcurrencyError: {exc}"),
        )
    except OwlTokenLimitError as exc:
        # Token-limit truncation is intentional — collected chunks stay in state.
        log.engine.warning(
            "[pipeline] execute: token limit reached — truncated",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
        )
    except Exception as exc:
        log.engine.error(
            "[pipeline] execute: provider stream failed",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
        )
        return state.evolve(errors=(*state.errors, f"execute: {type(exc).__name__}: {exc}"))

    duration_ms = (time.monotonic() - t0) * 1000
    log.engine.info(
        "[pipeline] execute: exit",
        extra={
            "_fields": {
                "trace_id": state.trace_id,
                "owl": state.owl_name,
                "chunks": len(chunks),
                "duration_ms": duration_ms,
                "guarded": manifest is not None,
            }
        },
    )
    return state.evolve(responses=(*state.responses, *chunks))
