"""Pipeline step 7: synthesize — render the final response text."""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState


async def run(state: PipelineState) -> PipelineState:
    log.engine.info(
        "[pipeline] synthesize: entry",
        extra={"_fields": {"trace_id": state.trace_id, "responses": len(state.responses)}},
    )
    log.engine.info("[pipeline] synthesize: exit", extra={"_fields": {"trace_id": state.trace_id}})
    return state
