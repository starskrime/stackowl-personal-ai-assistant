"""Pipeline step 8: deliver — write response chunks to the StreamRegistry."""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState


async def run(state: PipelineState) -> PipelineState:
    """Write accumulated response chunks to the session's StreamWriter.

    Retrieves the StreamRegistry from pipeline services context.
    Discards gracefully if no writer is registered — never raises.
    """
    # A delegated sub-pipeline (delegation_depth>0) shares the parent's session_id
    # but has NO user stream of its own — its result returns to the parent via the
    # A2A response (final_state.responses), not the user's StreamWriter. Delivering
    # here would write the child's raw text to the PARENT's stream and close it,
    # losing the parent's (footered) answer. Skip delivery for delegated children.
    if state.delegation_depth > 0:
        log.gateway.debug(
            "[pipeline] deliver: delegated sub-pipeline — skip user-stream delivery",
            extra={"_fields": {"session_id": state.session_id,
                               "delegation_depth": state.delegation_depth}},
        )
        return state

    services = get_services()
    registry = services.stream_registry
    log.gateway.info(
        "[pipeline] deliver: entry",
        extra={"_fields": {"session_id": state.session_id, "chunk_count": len(state.responses)}},
    )
    if registry is None:
        log.gateway.warning(
            "[pipeline] deliver: no registry in services — discarding responses",
            extra={"_fields": {"session_id": state.session_id}},
        )
        return state

    # Streams are keyed by request_id (== trace_id) so each concurrent turn owns
    # its own slot. A request_id with no registered writer is a HARD DROP — the
    # turn output is discarded loudly and NEVER rerouted to a default/other slot
    # (the response-side mirror of no-hidden-errors).
    writer = registry.get_writer(state.trace_id)
    if writer is None:
        log.gateway.warning(
            "[deliver] stream-miss: no writer for request_id; dropping turn output",
            extra={"_fields": {"request_id": state.trace_id, "session_id": state.session_id}},
        )
        return state

    # REACT-8/F037 — terminal signaling contract. The tool path (and consolidate)
    # build content chunks; ``StreamWriter.close()`` below appends the SINGLE
    # is_final=True sentinel (empty content) that ``StreamReader`` keys on to stop
    # (the reader BREAKS on is_final WITHOUT yielding it). So a content chunk must
    # NEVER carry is_final=True — the reader would swallow its content. The terminal
    # signal for the streaming path is the close() sentinel, not a per-content flag;
    # the cli_adapter/conversation_view is_final checks are satisfied by the adapter's
    # own belt-and-suspenders terminal marker. is_final on a CONTENT chunk is dead for
    # this path by design — kept only for the non-streaming consolidate merge.
    for chunk in state.responses:
        if chunk.trace_id and chunk.trace_id != state.trace_id:
            log.gateway.error(
                "[deliver] chunk request_id mismatch — hard drop, never reroute",
                extra={"_fields": {"chunk_request_id": chunk.trace_id, "turn_request_id": state.trace_id}},
            )
            continue
        # Stamp this turn's reply target onto the (frozen) chunk so a fan-out
        # channel (Telegram) routes the output back to ITS OWN chat under
        # concurrency. None for CLI turns — the adapter resolves the destination.
        await writer.write(chunk)
    await writer.close()

    log.gateway.info(
        "[pipeline] deliver: exit",
        extra={"_fields": {"session_id": state.session_id, "chunks_written": len(state.responses)}},
    )
    return state
