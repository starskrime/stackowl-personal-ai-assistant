"""The tier-escalation reset empties the tool-outcome ledger between attempts.

A discarded weak attempt may have recorded a failed consequential tool; if that
poison carried into the next tier's give-up floor, a turn the stronger tier
actually completed would be falsely floored. The reset run on every escalation
must clear it AND record the machinery recovery.
"""

from __future__ import annotations

from stackowl.infra import recovery_context, tool_outcome_ledger
from stackowl.pipeline.steps.execute import reset_ledger_for_tier_escalation


def test_reset_clears_ledger_and_records_recovery() -> None:
    led_token = tool_outcome_ledger.bind()
    rec_token = recovery_context.bind()
    try:
        # A discarded fast attempt recorded a failed consequential action.
        tool_outcome_ledger.record_tool_outcome(
            name="send_message", action_severity="consequential", success=False,
        )
        assert tool_outcome_ledger.consequential_tally() == (1, 0)

        reset_ledger_for_tier_escalation("fast", "standard", trace_id="t")

        # The next tier starts from a clean ledger — no inherited consequential failure.
        assert tool_outcome_ledger.get_outcomes() == ()
        assert tool_outcome_ledger.consequential_tally() == (0, 0)
        # The escalation is recorded as a machinery recovery (not user-facing).
        events = recovery_context.get_recovery()
        assert any(
            e.kind == "tier_escalation" and e.failed == "fast" and e.recovered_via == "standard"
            for e in events
        )
    finally:
        recovery_context.reset(rec_token)
        tool_outcome_ledger.reset(led_token)
