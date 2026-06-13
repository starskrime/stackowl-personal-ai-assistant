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

_EFFECTFUL = {"write", "consequential"}

# Recovery kind that bridges a consequential failure via a sibling tool.
# When a substitution succeeded, the capability gap was bridged — NOT a give-up.
_BRIDGING_RECOVERY_KINDS = {"substitution"}


async def surface_consequential_giveup_floor(state: PipelineState) -> PipelineState:
    """Replace a dressed-up give-up draft with an honest floor.

    1. ENTRY — read the turn ledger's consequential tally.
    2. DECISION — if no unachieved consequential outcome, no-op.
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
        cf, cs = tool_outcome_ledger.consequential_tally()
        # 2. DECISION — fast exits before the replace path
        if not is_unachieved_consequential_giveup(cons_failures=cf, cons_successes=cs):
            log.engine.debug(
                "[giveup_floor] surface_consequential_giveup_floor: no unachieved consequential — no-op",
                extra={"_fields": {"trace_id": state.trace_id, "cons_failures": cf, "cons_successes": cs}},
            )
            return state
        # A successful substitution bridges the capability gap — the sibling
        # achieved what the primary couldn't, so this is NOT a give-up.
        recovery_events = recovery_context.get_recovery()
        if any(e.kind in _BRIDGING_RECOVERY_KINDS and e.recovered_via for e in recovery_events):
            log.engine.debug(
                "[giveup_floor] surface_consequential_giveup_floor: substitution bridged gap — no-op",
                extra={"_fields": {"trace_id": state.trace_id, "n_recovery_events": len(recovery_events)}},
            )
            return state
        failed_name = next(
            (o.name for o in tool_outcome_ledger.get_outcomes()
             if o.action_severity in _EFFECTFUL and not o.success),
            None,
        )
        # 3. STEP — build honest floor (pure, deterministic, no model call)
        floor_text = synthesize_floor(
            goal=state.input_text,
            error=None,
            attempts=None,
            partial=None,
            failed_capability=failed_name,
        )
        log.engine.info(
            "[giveup_floor] consequential outcome not achieved — replacing draft with honest floor",
            extra={"_fields": {"trace_id": state.trace_id, "failed_capability": failed_name}},
        )
        chunk = ResponseChunk(
            content=floor_text,
            is_final=False,
            chunk_index=0,
            trace_id=state.trace_id,
            owl_name=state.owl_name,
        )
        # 4. EXIT — REPLACE the untrusted draft; never append
        return state.evolve(responses=(chunk,))
    except Exception as exc:  # B5 — never break delivery
        log.engine.error(
            "[giveup_floor] failed — leaving response untouched",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state
