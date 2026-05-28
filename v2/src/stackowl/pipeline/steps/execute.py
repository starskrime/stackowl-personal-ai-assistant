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


async def _run_with_tools(
    state: PipelineState,
    provider: ModelProvider,
    tool_registry: ToolRegistry,
) -> PipelineState:
    """Execute the provider's tool loop and return updated state."""
    tool_schemas = tool_registry.to_provider_schema(provider.protocol)
    log.engine.info(
        "[pipeline] execute: tool_loop entry",
        extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name, "tools": len(tool_schemas)}},
    )

    async def _dispatch(name: str, args: dict[str, object]) -> str:
        t = tool_registry.get(name)
        if t is None:
            log.engine.warning("[pipeline] execute: unknown tool in dispatch", extra={"_fields": {"tool": name}})
            return f"Tool not found: {name}"
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
        return tr.output if tr.success else (tr.error or tr.output)

    t0 = time.monotonic()
    try:
        final_text, raw_calls = await provider.complete_with_tools(
            user_text=state.input_text,
            system_text=state.memory_context,
            tool_schemas=tool_schemas,
            tool_dispatcher=_dispatch,
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

    messages: list[Message] = [Message(role="user", content=state.input_text)]
    if state.memory_context:
        messages = [Message(role="system", content=state.memory_context), *messages]

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
