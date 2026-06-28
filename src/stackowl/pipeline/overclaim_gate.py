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

    Two INDEPENDENT triggers (an affirmative non-floor draft fires the first that
    holds); both read MEASURED ledger truth, never the claim prose:

    1. MEASURED effect veto (ADR-T2 / TS3) — the turn invoked a tool that declared a
       durable ``effect_class`` (creates_persistent_entity / sends_message / schedules)
       whose result was NOT verified==True. DEFAULT-DENY: verified∈{False, unknown} or a
       plain failure all qualify (``state.unverified_effects`` is non-empty). The burden
       is on PROOF — absence of a verified receipt vetoes a "✅ done" claim regardless of
       how richly it is phrased, so it cannot be gamed by wording. ``unknown`` is NOT
       success — it routes to the floor.
    2. STRUCTURAL give-up (the original) — nothing crossed the OUT boundary
       (``delivered_successes`` empty) AND at least one tool failed/bounced (an
       unrecovered consequential failure OR a TPS no-progress bounce).

    The empty-draft and already-floor guards clear both. A pure conversational/clarify
    turn (no effect-classed tool, no failures, no no_progress_tools) is CLEARED.
    """
    if not state.responses or all(not c.content.strip() for c in state.responses):
        return (False, None)
    if any(getattr(c, "is_floor", False) for c in state.responses):
        return (False, None)
    # Trigger 1 — MEASURED: an unproven durable effect vetoes the affirmative draft
    # FIRST, before the delivery clear: a turn that delivered ONE thing but could not
    # prove it created the agent must still not claim the agent exists.
    if state.unverified_effects:
        return (True, state.unverified_effects[0])
    if state.delivered_successes:
        # Something crossed the OUT boundary — legitimate delivery.
        return (False, None)
    unrecovered = _unrecovered_consequential_failures(state)
    stuck_tools = state.no_progress_tools
    culprit = (
        next((n for n in state.consequential_failures if n in unrecovered), None)
        or (stuck_tools[0] if stuck_tools else None)
    )
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
            log.engine.debug(
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
