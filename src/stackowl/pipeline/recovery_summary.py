"""surface_recovery — pre-delivery render of machinery recovery events (pillar ④).

Sibling of ``surface_applied_lessons``: runs once per turn, before deliver, in
BOTH backends — so the explanation reaches every channel with no per-channel
duplication. Appends a line ONLY for ``user_visible`` recovery events AND only
when there is a real (non-floor) answer to annotate. Never raises.
"""

from __future__ import annotations

from stackowl.infra import recovery_context
from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.setup.localize import localize_format

_MAX_LINES = 2

_TEMPLATE_BY_KIND = {
    "substitution": "self_heal_recovery_note",          # slots: failed, recovered_via
    "provider_fallback": "self_heal_recovery_provider",  # generic, no slots
}

# F-10: when the final response is an honest floor we still surface ONE brief,
# GENERIC line so the user knows alternatives were tried before the give-up. No
# names are leaked (a floored turn is the worst time to expose internal tool names).
_ATTEMPTED_KEY = "self_heal_recovery_attempted"


async def surface_recovery(state: PipelineState) -> PipelineState:
    """Append one localized line per user-visible recovery (capped). Self-healing."""
    try:
        events = [e for e in recovery_context.get_recovery() if e.user_visible]
        if not events:
            return state
        lang = state.language  # F-9: honor the turn language (was hardcoded "en")
        has_real_answer = any(
            c.content.strip() and not c.is_floor for c in state.responses
        )
        if not has_real_answer:
            # F-10: a floored turn must NOT silently swallow the recovery trace.
            # When an honest floor is present, surface ONE brief, GENERIC line so
            # "I tried alternatives before giving up" is visible. With no floor to
            # annotate (e.g. empty response), there is nothing to surface alongside.
            has_floor = any(c.content.strip() and c.is_floor for c in state.responses)
            if not has_floor:
                log.engine.debug(
                    "[recovery_summary] skip — no answer or floor to annotate",
                    extra={"_fields": {"trace_id": state.trace_id, "n_events": len(events)}},
                )
                return state
            attempted = ResponseChunk(
                content=localize_format(_ATTEMPTED_KEY, lang),  # generic, no names
                is_final=False, chunk_index=len(state.responses),
                trace_id=state.trace_id, owl_name=state.owl_name,
            )
            log.engine.info(
                "[recovery_summary] surfaced attempted-recovery line alongside floor",
                extra={"_fields": {"trace_id": state.trace_id, "n_events": len(events)}},
            )
            return state.evolve(responses=(*state.responses, attempted))
        new_chunks: list[ResponseChunk] = []
        base_index = len(state.responses)
        for offset, e in enumerate(events[:_MAX_LINES]):
            key = _TEMPLATE_BY_KIND.get(e.kind)
            if key is None:
                log.engine.debug(
                    "[recovery_summary] skip — unmapped recovery kind",
                    extra={"_fields": {"trace_id": state.trace_id, "kind": e.kind}},
                )
                continue
            if key == "self_heal_recovery_provider":
                text = localize_format(key, lang)  # generic, no names
            else:
                text = localize_format(key, lang, failed=e.failed,
                                       recovered_via=e.recovered_via)
            # Annotation chunk appended after the real answer; is_final stays False
            # (not a terminal response).
            new_chunks.append(ResponseChunk(
                content=text, is_final=False, chunk_index=base_index + offset,
                trace_id=state.trace_id, owl_name=state.owl_name,
            ))
        log.engine.info(
            "[recovery_summary] surfaced recovery lines",
            extra={"_fields": {"trace_id": state.trace_id, "n": len(new_chunks)}},
        )
        return state.evolve(responses=(*state.responses, *new_chunks))
    except Exception as exc:  # B5 — never break delivery
        log.engine.error(
            "[recovery_summary] surfacing failed — leaving response untouched",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state
