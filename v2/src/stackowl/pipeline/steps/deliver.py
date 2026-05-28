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

    writer = registry.get_writer(state.session_id)
    if writer is None:
        log.gateway.warning(
            "[pipeline] deliver: no writer for session — discarding",
            extra={"_fields": {"session_id": state.session_id}},
        )
        return state

    for chunk in state.responses:
        await writer.write(chunk)
    await writer.close()

    log.gateway.info(
        "[pipeline] deliver: exit",
        extra={"_fields": {"session_id": state.session_id, "chunks_written": len(state.responses)}},
    )
    return state
