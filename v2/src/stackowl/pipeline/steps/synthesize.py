"""Pipeline step 7: synthesize — render the final response text."""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState


async def run(state: PipelineState) -> PipelineState:
    log.engine.debug("[pipeline] synthesize: pass-through (not yet implemented)")
    return state
