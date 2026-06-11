"""surface_applied_lessons — pre-delivery render of the model's applied-lesson
self-reports (pillar ④). Sibling to ``surface_critical_failure``: runs once per
turn, before deliver, in BOTH backends — so the explanation reaches every channel
with no per-channel duplication.

Honesty: appends a line ONLY when (a) the model called ``note_applied_lesson``
this turn AND (b) there is a real (non-floor) answer to annotate. Never raises.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.pipeline import lesson_context as lc
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.setup.localize import localize_format

_MAX_LINES = 2
_LANG = "en"  # turn language plumbing is out of scope; localize falls back to en


async def surface_applied_lessons(state: PipelineState) -> PipelineState:
    """Append one localized line per applied lesson (capped). Self-healing."""
    try:
        applied = lc.drain_applied()
        if not applied:
            return state
        has_real_answer = any(
            c.content.strip() and not c.is_floor for c in state.responses
        )
        if not has_real_answer:
            log.engine.debug(
                "[applied_lessons] skip — no real answer to annotate",
                extra={"_fields": {"trace_id": state.trace_id, "n_applied": len(applied)}},
            )
            return state
        new_chunks: list[ResponseChunk] = []
        base_index = len(state.responses)
        for offset, a in enumerate(applied[:_MAX_LINES]):
            text = localize_format("self_heal_applied_lesson", _LANG, what_you_did=a.what_you_did)
            new_chunks.append(ResponseChunk(
                content=text, is_final=False, chunk_index=base_index + offset,
                trace_id=state.trace_id, owl_name=state.owl_name,
            ))
        log.engine.info(
            "[applied_lessons] surfaced applied-lesson lines",
            extra={"_fields": {"trace_id": state.trace_id, "n": len(new_chunks)}},
        )
        return state.evolve(responses=(*state.responses, *new_chunks))
    except Exception as exc:  # B5 — never break delivery
        log.engine.error(
            "[applied_lessons] surfacing failed — leaving response untouched",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state
