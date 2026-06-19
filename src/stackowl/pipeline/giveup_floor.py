"""surface_consequential_giveup_floor — replace a dressed-up give-up with an honest floor.

When the turn ledger shows a consequential/write action was attempted and FAILED
with NO consequential success (the outcome was not achieved), the model's draft
cannot be trusted to be honest about it — so REPLACE the responses with the
deterministic honest floor naming the failed capability. Runs pre-delivery in both
backends, BEFORE surface_critical_failure. Judge-INDEPENDENT (reads the ledger,
not the persistence judge). Never raises.
"""

from __future__ import annotations

from stackowl.infra import recovery_context, tool_outcome_ledger
from stackowl.infra.observability import log
from stackowl.pipeline.persistence import is_unachieved_consequential_giveup
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.pipeline.supervisor import synthesize_floor

# Recovery kind that bridges a consequential failure via a sibling tool.
# When a substitution succeeded, the capability gap was bridged — NOT a give-up.
_BRIDGING_RECOVERY_KINDS = {"substitution"}


def _unrecovered_consequential_failures(
    state: PipelineState | None = None,
) -> set[str]:
    """Names of consequential/write tools that FAILED this turn and were NOT
    bridged by a successful substitution. Empty ⇒ every effect was achieved or
    recovered.

    REACT-7/F099 — when ``state`` carries the consequential SNAPSHOT (stamped by
    execute while the ledger was live), read it instead of the ambient ContextVars,
    so the honesty decision does not depend on the bind() lifetime spanning this
    call. Falls back to the live ledger/recovery context when no snapshot was taken
    (byte-identical to the original path)."""
    if state is not None and state.has_consequential_snapshot:
        failed = set(state.consequential_failures)
        recovered = set(state.recovered_consequential)
        return failed - recovered
    failed = {
        o.name for o in tool_outcome_ledger.get_outcomes()
        if tool_outcome_ledger.is_effectful_failure(
            o.action_severity, o.success, o.side_effect_committed,
        )
    }
    recovered = {
        e.failed for e in recovery_context.get_recovery()
        if e.kind in _BRIDGING_RECOVERY_KINDS and e.recovered_via
    }
    return failed - recovered


def is_consequential_giveup_now(state: PipelineState | None = None) -> bool:
    """True iff a consequential/write action was attempted-and-failed with NO
    consequential success AND at least one such failure was not bridged by a
    capability substitution this turn.

    REACT-7/F099 — when ``state`` carries the consequential snapshot, the tally is
    read from immutable state (not the ambient ledger ContextVar). Falls back to the
    live ledger when no snapshot was taken. Never raises. The SINGLE source of truth
    for both the nudge veto and the terminal floor."""
    try:
        if state is not None and state.has_consequential_snapshot:
            cf = len(state.consequential_failures)
            # GOAL-RELEVANT ACCOUNTING (P0 budget-cap overclaim fix). On a turn cut off
            # by the BUDGET CAP, an incidental local-workspace FILE mutation (write_file /
            # edit / apply_patch / undo_write) is NOT the user's delivered outcome — it
            # never crossed the boundary OUT. So at the budget-cap terminal path the
            # success tally is the DELIVERED subset (every effectful success EXCEPT those
            # local file mutations — consequential sends AND boundary-crossing dispatches
            # like delegate_task / sessions_* DO count). An incidental local write alongside
            # a consequential failure no longer disarms the honest floor; a turn that
            # genuinely dispatched delegated work is NOT floored. A CLEAN model-chosen stop
            # is trusted and keeps the full effectful-success tally (byte-identical to
            # today). The shared nudge-veto predicate (is_unachieved_consequential_giveup)
            # is unchanged either way.
            cs = (
                len(state.delivered_successes)
                if state.budget_capped
                else len(state.consequential_successes)
            )
        else:
            cf, cs = tool_outcome_ledger.consequential_tally()
        if not is_unachieved_consequential_giveup(cons_failures=cf, cons_successes=cs):
            return False
        # Every failed consequential must be individually bridged — a single
        # substitution does NOT cover sibling failures (per-tool recovery check).
        return bool(_unrecovered_consequential_failures(state))
    except Exception as exc:  # never raise into the loop / delivery
        log.engine.error(
            "[giveup_floor] is_consequential_giveup_now failed",
            exc_info=exc,
        )
        return False


def _floor_chunk(state: PipelineState, failed_name: str | None) -> ResponseChunk:
    """Build an is_floor=True honest-floor ResponseChunk naming ``failed_name``.

    Pure, deterministic, no model call. Shared by the consequential-giveup path and
    the no-progress-giveup path (Task 4) so the chunk shape stays byte-identical."""
    floor_text = synthesize_floor(
        goal=state.input_text,
        error=None,
        attempts=None,
        partial=None,
        failed_capability=failed_name,
        lang=state.language,  # F089/F098 — localize the provider-down floor
    )
    return ResponseChunk(
        content=floor_text,
        is_final=False,
        chunk_index=0,
        trace_id=state.trace_id,
        owl_name=state.owl_name,
        # SP-1 — floor-origin marker. Lets persist (F088) skip the floor prose
        # as a promotable fact, keeps the critical-failure cascade from treating
        # this honest floor as a genuine answer, and lets the pipeline floor band
        # recognize a provider floor as replaceable (no double floor).
        is_floor=True,
    )


def is_no_progress_giveup(state: PipelineState) -> bool:
    """True iff the turn made NO forward progress, delivered nothing to the user,
    and at least one tool was bounced for no-progress — i.e. the model spiraled and
    its draft cannot be trusted. INDEPENDENT of the consequential ledger (covers the
    G2 pure-refusal shape the consequential floor misses). turn_made_progress
    defaults True, so a non-tool / progressing / conversational turn is never caught."""
    try:
        if state.turn_made_progress:
            return False
        if not state.no_progress_tools:
            return False
        if state.delivered_successes:   # something crossed the boundary OUT → not a give-up
            return False
        # Don't double-floor: if the existing responses are already a floor, no-op.
        return not any(getattr(c, "is_floor", False) for c in state.responses)
    except Exception as exc:
        log.engine.error("[giveup_floor] is_no_progress_giveup failed", exc_info=exc)
        return False


async def surface_consequential_giveup_floor(state: PipelineState) -> PipelineState:
    """Replace a dressed-up give-up draft with an honest floor.

    1. ENTRY — read the turn ledger's consequential tally.
    2. DECISION — if no unachieved consequential outcome, check no-progress path.
    3. STEP — synthesize honest floor naming the failed capability.
    4. EXIT — return evolved state with responses REPLACED.
    B5 catch: never raises; logs on failure and returns state untouched.
    """
    try:
        # 1. ENTRY
        log.engine.debug(
            "[giveup_floor] surface_consequential_giveup_floor: entry",
            extra={"_fields": {"trace_id": state.trace_id, "n_responses": len(state.responses)}},
        )
        # 2. DECISION — fast exit: shared predicate covers ledger tally + substitution guard.
        # Prefer the state snapshot (F099) so the decision rides immutable state.
        if not is_consequential_giveup_now(state):
            log.engine.debug(
                "[giveup_floor] surface_consequential_giveup_floor: no unachieved consequential — no-op",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            # G2 honesty gap: no consequential failure, but the turn may have spiraled
            # (same tool bounced repeatedly with no progress and nothing delivered).
            if is_no_progress_giveup(state):
                log.engine.info(
                    "[giveup_floor] no forward progress — replacing draft with honest floor",
                    extra={"_fields": {
                        "trace_id": state.trace_id,
                        "failed_capability": state.no_progress_tools[0],
                    }},
                )
                return state.evolve(responses=(_floor_chunk(state, state.no_progress_tools[0]),))
            return state
        unrecovered = _unrecovered_consequential_failures(state)
        if state.has_consequential_snapshot:
            # Name from the snapshot's ordered failures (first unrecovered).
            failed_name = next(
                (n for n in state.consequential_failures if n in unrecovered), None,
            )
        else:
            failed_name = next(
                (o.name for o in tool_outcome_ledger.get_outcomes()
                 if tool_outcome_ledger.is_effectful_failure(
                     o.action_severity, o.success, o.side_effect_committed,
                 ) and o.name in unrecovered),
                None,
            )
        # 3. STEP — build honest floor (pure, deterministic, no model call)
        log.engine.info(
            "[giveup_floor] consequential outcome not achieved — replacing draft with honest floor",
            extra={"_fields": {"trace_id": state.trace_id, "failed_capability": failed_name}},
        )
        # 4. EXIT — REPLACE the untrusted draft; never append
        return state.evolve(responses=(_floor_chunk(state, failed_name),))
    except Exception as exc:  # B5 — never break delivery
        log.engine.error(
            "[giveup_floor] failed — leaving response untouched",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state
