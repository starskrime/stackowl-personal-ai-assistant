"""PA5(a) — lying-success gate (judge-INDEPENDENT structural veto).

A "lying success" is an effect-classed tool that reported ``success=True`` but whose
MEASURED verdict is NOT ``True`` (``verified=False`` — claimed-but-refuted — or
``verified=None`` — unknown / default-deny). The turn must NOT ship as a delivered
success: the confident draft is REPLACED with the honest floor (``is_floor=True``)
naming the failed capability.

The point of this gate: the structural delivery band (``surface_consequential_giveup_floor``
+ ``surface_overclaim_gate``) reads the turn LEDGER, not the persistence judge — so it
catches the lie even when the judge is unavailable. We prove that by stubbing the judge
(:func:`stackowl.pipeline.persistence.judge_delivery`) to RAISE and still getting the
honest floor. Assertions are on observable STATE (responses / is_floor), never a log.

Ledger fixture reuses the EXACT pattern in test_overclaim_gate's
``test_snapshot_maps_effect_verification``: bind the ledger, record the outcome, and
let ``_snapshot_consequential`` stamp the immutable state the band reads.
"""

from __future__ import annotations

import pytest

from stackowl.infra import tool_outcome_ledger
from stackowl.pipeline.giveup_floor import surface_consequential_giveup_floor
from stackowl.pipeline.overclaim_gate import surface_overclaim_gate
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _snapshot_consequential
from stackowl.pipeline.streaming import ResponseChunk

# A rich, confident affirmative draft. The veto must NOT need to read these words —
# it keys on the unverified/refuted effect in the ledger, not the prose.
_RICH_CLAIM = "✅ Done! I've sent your weekly report to the whole team."


def _state(**kw: object) -> PipelineState:
    base: dict[str, object] = dict(
        trace_id="t-lying",
        session_id="s",
        input_text="send my report to the team",
        channel="cli",
        owl_name="o",
        pipeline_step="execute",
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


def _draft(content: str) -> ResponseChunk:
    return ResponseChunk(
        content=content, is_final=False, chunk_index=0,
        trace_id="t-lying", owl_name="o", is_floor=False,
    )


def _lying_success_state(*, verified: bool | None) -> PipelineState:
    """Snapshot a turn where an effect-classed tool reported success but the world-read
    did NOT confirm it (verified is not True). Returns the immutable state the delivery
    band reads — exactly as execute.py stamps it after the loop."""
    token = tool_outcome_ledger.bind()
    try:
        tool_outcome_ledger.record_tool_outcome(
            name="send_message",
            action_severity="consequential",
            success=True,            # the tool CLAIMED it worked …
            verified=verified,       # … but reality says otherwise (False/unknown)
            effect_class="sends_message",
        )
        return _snapshot_consequential(_state(responses=(_draft(_RICH_CLAIM),)))
    finally:
        tool_outcome_ledger.reset(token)


def _explode(*_a: object, **_k: object) -> object:
    raise RuntimeError("persistence judge is unavailable (PA5 lying-success gate)")


def test_judge_stub_actually_raises() -> None:
    """Non-vacuous guard: the stub we install genuinely makes the judge explode, so a
    passing band below proves judge-INDEPENDENCE, not a no-op stub."""
    with pytest.raises(RuntimeError):
        _explode("draft", [])


@pytest.mark.asyncio
async def test_refuted_effect_floors_without_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    """verified=False (claimed sent, reality refuted) → the delivery band replaces the
    confident draft with the honest floor, with the persistence judge stubbed to RAISE."""
    monkeypatch.setattr("stackowl.pipeline.persistence.judge_delivery", _explode)

    state = _lying_success_state(verified=False)
    # The lie is in the ledger snapshot, on observable state.
    assert "send_message" in state.consequential_failures
    assert "send_message" in state.unverified_effects

    # Drive the band in backend order: giveup floor, then overclaim gate.
    after_floor = await surface_consequential_giveup_floor(state)
    final = await surface_overclaim_gate(after_floor)

    chunk = final.responses[0]
    assert chunk.is_floor is True, "a refuted effect must ship the honest floor"
    assert chunk.content != _RICH_CLAIM
    assert "sent" not in chunk.content.lower()  # the false claim is gone
    assert "send_message" not in (final.delivered_successes or ())


@pytest.mark.asyncio
async def test_unknown_receipt_floors_via_overclaim_without_judge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """verified=None (unknown — no receipt) is DEFAULT-DENY: not an effectful failure
    (the giveup floor stays its hand) but the overclaim gate floors it via
    ``unverified_effects`` — proving the effect_class veto path itself, judge raising."""
    monkeypatch.setattr("stackowl.pipeline.persistence.judge_delivery", _explode)

    state = _lying_success_state(verified=None)
    # unknown ⇒ NOT a counted failure, but IS an unproven effect.
    assert "send_message" not in state.consequential_failures
    assert "send_message" in state.unverified_effects

    after_floor = await surface_consequential_giveup_floor(state)
    assert after_floor.responses[0].content == _RICH_CLAIM  # giveup floor no-ops

    final = await surface_overclaim_gate(after_floor)
    assert final.overclaim_blocked is True
    assert final.responses[0].is_floor is True
    assert final.responses[0].content != _RICH_CLAIM


@pytest.mark.asyncio
async def test_verified_success_is_not_floored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: a genuinely verified effect (verified=True) is NOT a lying success — the
    draft ships unchanged. Proves the gate floors the LIE, not every effect-classed turn."""
    monkeypatch.setattr("stackowl.pipeline.persistence.judge_delivery", _explode)

    token = tool_outcome_ledger.bind()
    try:
        tool_outcome_ledger.record_tool_outcome(
            name="send_message", action_severity="consequential",
            success=True, verified=True, effect_class="sends_message",
        )
        state = _snapshot_consequential(_state(responses=(_draft(_RICH_CLAIM),)))
    finally:
        tool_outcome_ledger.reset(token)

    assert state.unverified_effects == ()
    after_floor = await surface_consequential_giveup_floor(state)
    final = await surface_overclaim_gate(after_floor)
    assert final.responses[0].is_floor is False
    assert final.responses[0].content == _RICH_CLAIM
