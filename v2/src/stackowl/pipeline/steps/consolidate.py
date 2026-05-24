"""Pipeline step 6: consolidate — merge owl outputs and tool results."""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState


async def run(state: PipelineState) -> PipelineState:
    log.engine.debug("[pipeline] consolidate: pass-through (not yet implemented)")
    return state
