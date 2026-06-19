"""surface_overclaim_gate — block a confident non-floor response that delivered
nothing real while tools failed/bounced. STRUCTURAL (no fragile text analysis):
reuses delivered_successes (P0) + the TPS no_progress stamp. Runs AFTER the
give-up floor, BEFORE deliver, in both backends. Never raises. Emits structured
overclaim.detected / overclaim.cleared so a dead gate is visible.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.pipeline.giveup_floor import _floor_chunk, _unrecovered_consequential_failures
from stackowl.pipeline.state import PipelineState


def _is_overclaim(state: PipelineState) -> tuple[bool, str | None]:
    """Return (True, culprit) if the current draft is a structural overclaim.

    Conditions (ALL must hold):
    - draft responses are non-empty
    - no response is already an honest floor (is_floor=True)
    - delivered_successes is empty (nothing crossed the OUT boundary)
    - at least one tool failed/bounced: unrecovered consequential failure OR
      a no-progress bounce recorded by the TPS tracker

    A pure conversational/clarify turn (0 tool calls, no failures, no no_progress_tools)
    is CLEARED — never blocked.
    """
    if not state.responses or all(not c.content.strip() for c in state.responses):
        return (False, None)
    if any(getattr(c, "is_floor", False) for c in state.responses):
        return (False, None)
    if state.delivered_successes:
        # Something crossed the OUT boundary — legitimate delivery.
        return (False, None)
    unrecovered = _unrecovered_consequential_failures(state)
    stuck = set(state.no_progress_tools)
    culprit = next(iter(unrecovered), None) or next(iter(stuck), None)
    if culprit is None:
        # No tool failed and no tool bounced — not an overclaim.
        return (False, None)
    return (True, culprit)


async def surface_overclaim_gate(state: PipelineState) -> PipelineState:
    """Replace a confident overclaim draft with an honest floor.

    Called AFTER surface_consequential_giveup_floor and BEFORE persist_turn /
    deliver in both backends. Never raises — any internal error is logged and the
    original state is returned unchanged (fail-open: no silent suppression of a
    valid response).
    """
    try:
        is_oc, culprit = _is_overclaim(state)
        if not is_oc:
            log.engine.info(
                "overclaim.cleared",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            return state
        log.engine.warning(
            "overclaim.detected",
            extra={
                "_fields": {
                    "trace_id": state.trace_id,
                    "failed_capability": culprit,
                }
            },
        )
        floor = _floor_chunk(state, culprit)
        return state.evolve(responses=(floor,), overclaim_blocked=True)
    except Exception as exc:
        log.engine.error(
            "[overclaim_gate] internal error — leaving response untouched",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state
