"""Pipeline step 6: consolidate — merge owl outputs and tool results, then persist the turn."""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState


async def _persist_turn(state: PipelineState) -> None:
    """Best-effort: store the user+assistant turn as a staged conversation fact.

    Never raises — memory persistence MUST NOT block delivery. The dream worker
    later promotes these to committed_facts.
    """
    services = get_services()
    bridge = services.memory_bridge
    if bridge is None:
        return
    assistant_text = "\n".join(c.content for c in state.responses if c.content).strip()
    if not state.input_text and not assistant_text:
        return
    content = f"User: {state.input_text}\n\nAssistant: {assistant_text}"
    try:
        await bridge.store(content, state.session_id)
    except Exception as exc:
        log.memory.warning(
            "[pipeline] consolidate: persist_turn failed — skipping",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "session_id": state.session_id}},
        )


async def run(state: PipelineState) -> PipelineState:
    log.engine.info(
        "[pipeline] consolidate: entry",
        extra={"_fields": {
            "trace_id": state.trace_id,
            "responses": len(state.responses),
            "tool_calls": len(state.tool_calls),
        }},
    )
    out_state = state
    # Merge tool results into responses when tool_calls produced content but responses is empty.
    if state.tool_calls and not state.responses:
        from stackowl.pipeline.streaming import ResponseChunk
        combined = "\n\n".join(
            tc.result for tc in state.tool_calls if tc.result
        )
        if combined:
            chunk = ResponseChunk(
                content=combined,
                is_final=True,
                chunk_index=0,
                trace_id=state.trace_id,
                owl_name=state.owl_name,
            )
            log.engine.info(
                "[pipeline] consolidate: merged tool results",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            out_state = state.evolve(responses=(chunk,))
    # Persist the turn AFTER any merge so we capture the final assistant text.
    await _persist_turn(out_state)
    log.engine.info(
        "[pipeline] consolidate: exit",
        extra={"_fields": {"trace_id": state.trace_id}},
    )
    return out_state
