"""Pipeline step 8: deliver — write response chunks to the StreamRegistry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.pipeline.services import StepServices


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
    # its own slot. A request_id with no registered writer is a stream-MISS — the
    # live reader is gone (terminal disconnected mid-turn, or the slot was reaped).
    # The output is NEVER rerouted to a default/other slot (the response-side
    # mirror of no-hidden-errors), but STEER-2/F100: a computed top-level answer
    # must not be silently DROPPED. When this turn has a durable channel reply
    # target, fall back to a proactive send via that target so the answer still
    # reaches the user. CLI/single-terminal turns (no reply_target) have no durable
    # destination to push to — there the miss IS terminal and is logged loudly.
    writer = registry.get_writer(state.trace_id)
    if writer is None:
        await _proactive_fallback(state, services)
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
        chunk = chunk.model_copy(update={"target": state.reply_target})
        await writer.write(chunk)
    await writer.close()

    log.gateway.info(
        "[pipeline] deliver: exit",
        extra={"_fields": {"session_id": state.session_id, "chunks_written": len(state.responses)}},
    )
    return state


async def _proactive_fallback(state: PipelineState, services: StepServices) -> None:
    """Durably push a top-level turn's answer when its live stream is gone (F100).

    Called ONLY on a stream-miss for a non-delegated top-level turn (the caller
    already excludes ``delegation_depth>0``). The computed answer is joined and
    handed to the :class:`ProactiveDeliverer`, addressed via THIS turn's own
    ``reply_target`` (the per-turn destination — never the adapter's shared
    mutable ``_last_*``) at ``critical`` urgency so a direct answer is never
    quiet-hours-batched or suppressed away. Self-healing (B5): a missing
    deliverer, a turn with no durable reply target, or a deliverer that raises is
    logged loudly and swallowed — the fallback can never crash the pipeline. When
    no durable push is possible the miss is terminal and is logged as such (the
    response-side mirror of no-hidden-errors).
    """
    deliverer = services.proactive_deliverer
    body = "".join(c.content for c in state.responses if c.content)
    # A CLI / single-terminal turn owns no durable channel target; the adapter
    # resolved the destination, so a missing live writer there is a true terminal
    # miss with nowhere to push. Likewise an empty body or no deliverer.
    if deliverer is None or state.reply_target is None or not body:
        log.gateway.warning(
            "[deliver] stream-miss: no durable fallback available — answer not delivered",
            extra={
                "_fields": {
                    "request_id": state.trace_id,
                    "session_id": state.session_id,
                    "has_deliverer": deliverer is not None,
                    "has_target": state.reply_target is not None,
                    "body_len": len(body),
                }
            },
        )
        return

    # Import locally so the typing-only services import stays light and there is
    # no import cycle at module load (notifications imports pipeline types).
    from stackowl.notifications.router import Notification

    note = Notification(
        message=body,
        urgency="critical",  # a direct answer must not be batched/suppressed away
        category="turn_answer",
        channel_name=state.channel,
        target=state.reply_target,
    )
    try:
        status = await deliverer.deliver(note)
    except Exception as exc:  # B5 — the fallback must never crash the pipeline.
        log.gateway.error(
            "[deliver] stream-miss: proactive fallback raised — answer not delivered",
            exc_info=exc,
            extra={"_fields": {"request_id": state.trace_id, "session_id": state.session_id}},
        )
        return
    log.gateway.warning(
        "[deliver] stream-miss: live reader gone — answer delivered via proactive fallback",
        extra={
            "_fields": {
                "request_id": state.trace_id,
                "session_id": state.session_id,
                "channel": state.channel,
                "status": status,
                "body_len": len(body),
            }
        },
    )
