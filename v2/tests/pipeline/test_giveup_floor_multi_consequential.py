"""Multi-consequential substitution masking bug tests.

Bug: is_consequential_giveup_now() used ``any(...)`` over ALL recovery events,
so if consequential tool A failed+was-bridged-by-substitution BUT consequential
tool B also failed with NO recovery, the predicate returned False (not a give-up).
That masked B's unachieved consequential outcome and allowed a dressed-up give-up
about B to be delivered.

Additionally, surface_consequential_giveup_floor selected the FIRST failed
effectful tool by get_outcomes() order — which could be A (the recovered one),
naming the wrong (lying) tool in the honest floor.

These three tests guard both properties:
1. An unrecovered second consequential failure is still a give-up (predicate=True).
2. All consequential failures recovered via substitution → not a give-up (predicate=False, control).
3. The honest floor names the UNRECOVERED failure, never the recovered one.
"""

from __future__ import annotations

import pytest

from stackowl.infra import recovery_context
from stackowl.infra import tool_outcome_ledger as tol
from stackowl.pipeline.giveup_floor import (
    is_consequential_giveup_now,
    surface_consequential_giveup_floor,
)
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _state(text: str = "send both emails") -> PipelineState:
    return PipelineState(
        trace_id="t",
        session_id="s",
        input_text=text,
        channel="cli",
        owl_name="secretary",
        pipeline_step="deliver",
        responses=(
            ResponseChunk(
                content=text,
                is_final=False,
                chunk_index=0,
                trace_id="t",
                owl_name="secretary",
            ),
        ),
    )


def test_unrecovered_second_consequential_still_giveup() -> None:
    """BUG REGRESSION: A failed+recovered A and an unrecovered B → give-up is True.

    Under the OLD code the ``any(substitution recovery exists)`` made the predicate
    return False, masking B's unachieved consequential outcome. This test must FAIL
    before the fix and PASS after it.
    """
    tol_token = tol.bind()
    rc_token = recovery_context.bind()
    try:
        # Consequential tool A failed — recovered via substitution.
        tol.record_tool_outcome(name="send_a", action_severity="consequential", success=False)
        recovery_context.record_recovery(
            kind="substitution",
            failed="send_a",
            recovered_via="send_a_alt",
            user_visible=True,
        )
        # Consequential tool B failed — NO recovery.
        tol.record_tool_outcome(name="send_b", action_severity="consequential", success=False)

        assert is_consequential_giveup_now() is True, (
            "BUG: unrecovered send_b was masked by send_a's substitution recovery — "
            "is_consequential_giveup_now() returned False when it must return True"
        )
    finally:
        tol.reset(tol_token)
        recovery_context.reset(rc_token)


def test_all_consequential_failures_recovered_not_giveup() -> None:
    """Control: single consequential failure bridged by substitution → not a give-up.

    This behaviour is unchanged by the fix — all failures are recovered, so
    is_consequential_giveup_now() must remain False.
    """
    tol_token = tol.bind()
    rc_token = recovery_context.bind()
    try:
        # A failed, bridged by a substitution sibling.
        tol.record_tool_outcome(name="send_a", action_severity="consequential", success=False)
        recovery_context.record_recovery(
            kind="substitution",
            failed="send_a",
            recovered_via="send_a_alt",
            user_visible=True,
        )

        assert is_consequential_giveup_now() is False, (
            "False-positive guard: send_a was fully recovered via substitution — "
            "is_consequential_giveup_now() must return False"
        )
    finally:
        tol.reset(tol_token)
        recovery_context.reset(rc_token)


@pytest.mark.asyncio
async def test_floor_names_unrecovered_failure_not_recovered_one() -> None:
    """The honest floor must name the UNRECOVERED tool, never the recovered one.

    With A recovered (send_a) and B unrecovered (send_b), the old code picked the
    FIRST failed effectful tool by get_outcomes() order — which is send_a (the one
    that SUCCEEDED via substitution). That would be a lie in the floor text.
    After the fix, the floor must name send_b (the unrecovered failure).
    """
    tol_token = tol.bind()
    rc_token = recovery_context.bind()
    try:
        # A failed + recovered (recorded first so it appears first in get_outcomes()).
        tol.record_tool_outcome(name="send_a", action_severity="consequential", success=False)
        recovery_context.record_recovery(
            kind="substitution",
            failed="send_a",
            recovered_via="send_a_alt",
            user_visible=True,
        )
        # B failed, no recovery (recorded second — after A in order).
        tol.record_tool_outcome(name="send_b", action_severity="consequential", success=False)

        s = _state("send both emails")
        out = await surface_consequential_giveup_floor(s)
        delivered = "".join(c.content for c in out.responses)

        # The floor must have REPLACED the draft (non-empty, different from the input).
        assert delivered.strip(), "Floor produced empty text"
        assert "send both emails" not in delivered or "couldn" in delivered.lower(), (
            "Floor did not replace the draft"
        )

        # The floor must name the UNRECOVERED tool (send_b), not the recovered one (send_a).
        assert "send_b" in delivered, (
            f"Floor did not name the unrecovered tool 'send_b'. Delivered: {delivered!r}"
        )
        assert "send_a" not in delivered, (
            f"Floor incorrectly named the recovered tool 'send_a'. Delivered: {delivered!r}"
        )
    finally:
        tol.reset(tol_token)
        recovery_context.reset(rc_token)
