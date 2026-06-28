"""Unit tests for surface_overclaim_gate (Task 6 — Turn Progress Supervisor).

Covers the structural overclaim delivery-gate: a confident non-floor draft that
delivered nothing while a tool failed/bounced must be replaced with the honest
floor.  Runs AFTER surface_consequential_giveup_floor in both backends.
"""

from __future__ import annotations

from typing import get_args

import pytest

from stackowl.pipeline.overclaim_gate import _is_overclaim, surface_overclaim_gate
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk  # noqa: F401
from stackowl.tools.base import ToolManifest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(**kw: object) -> PipelineState:
    base: dict[str, object] = dict(
        trace_id="t-gate",
        session_id="s",
        input_text="send my report",
        channel="cli",
        owl_name="o",
        pipeline_step="execute",
        # default: turn made progress, no failures — cleared
        turn_made_progress=True,
        no_progress_tools=(),
        consequential_failures=(),
        consequential_snapshot_taken=False,
        delivered_successes=(),
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


def _draft(content: str, *, is_floor: bool = False) -> ResponseChunk:
    return ResponseChunk(
        content=content,
        is_final=False,
        chunk_index=0,
        trace_id="t-gate",
        owl_name="o",
        is_floor=is_floor,
    )


# ---------------------------------------------------------------------------
# Blocked cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overclaim_blocked() -> None:
    """Consequential failure + nothing delivered + non-floor draft → gate blocks."""
    state = _state(
        responses=(_draft("I've successfully sent your report!"),),
        consequential_failures=("send_image",),
        consequential_snapshot_taken=True,
        delivered_successes=(),
    )
    result = await surface_overclaim_gate(state)
    assert result.overclaim_blocked is True
    assert len(result.responses) == 1
    chunk = result.responses[0]
    assert chunk.is_floor is True
    # Overclaim text must be gone.
    assert "successfully" not in chunk.content.lower()


@pytest.mark.asyncio
async def test_no_progress_overclaim_blocked() -> None:
    """no_progress_tools + nothing delivered + non-floor draft → gate blocks."""
    state = _state(
        responses=(_draft("Task completed! Your code is running."),),
        turn_made_progress=False,
        no_progress_tools=("execute_code",),
        delivered_successes=(),
    )
    result = await surface_overclaim_gate(state)
    assert result.overclaim_blocked is True
    chunk = result.responses[0]
    assert chunk.is_floor is True


# ---------------------------------------------------------------------------
# Not-blocked cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delivered_success_not_blocked() -> None:
    """delivered_successes non-empty → legitimate delivery, NOT blocked."""
    state = _state(
        responses=(_draft("Your report has been sent!"),),
        consequential_failures=("send_image",),
        consequential_snapshot_taken=True,
        delivered_successes=("send_image",),
    )
    result = await surface_overclaim_gate(state)
    assert result.overclaim_blocked is False
    assert result.responses[0].is_floor is False
    assert "Your report has been sent!" in result.responses[0].content


@pytest.mark.asyncio
async def test_conversational_zero_tool_not_blocked() -> None:
    """Pure conversational turn (no failures, no no_progress_tools) → CLEARED."""
    state = _state(
        responses=(_draft("Sure, I can help with that!"),),
        # defaults: consequential_failures=(), no_progress_tools=(), delivered_successes=()
    )
    result = await surface_overclaim_gate(state)
    assert result.overclaim_blocked is False
    assert result.responses[0].content == "Sure, I can help with that!"


@pytest.mark.asyncio
async def test_already_floor_not_double_processed() -> None:
    """Already-honest floor draft → returned untouched (no double floor)."""
    floor_chunk = _draft("I wasn't able to complete that.", is_floor=True)
    state = _state(
        responses=(floor_chunk,),
        consequential_failures=("send_image",),
        consequential_snapshot_taken=True,
        delivered_successes=(),
    )
    result = await surface_overclaim_gate(state)
    assert result.overclaim_blocked is False
    assert result.responses[0] is floor_chunk


@pytest.mark.asyncio
async def test_empty_response_not_blocked() -> None:
    """Empty/whitespace-only response + consequential failure → CLEARED (no overclaim)."""
    state = _state(
        responses=(_draft("   "),),  # whitespace only
        consequential_failures=("send_image",),
        consequential_snapshot_taken=True,
        delivered_successes=(),
    )
    result = await surface_overclaim_gate(state)
    # Empty response guard clears it before the failure check
    assert result.overclaim_blocked is False
    assert result.responses[0].content == "   "


# ---------------------------------------------------------------------------
# ADR-T2 / TS3 — MEASURED ledger-driven veto (effect_class, not prose)
# ---------------------------------------------------------------------------
# These assert on LEDGER FACTS (state.unverified_effects, the snapshot of every
# effect-classed tool whose result was not verified==True) — never on the answer
# text. A rich "✅ deployed" draft is floored solely because the effect lacks a
# verified receipt; the same draft passes when a receipt exists. No keyword scan.


# A deliberately rich, confident affirmative draft. The gate must NOT need to read
# any of these words — it keys on the unverified effect, not the prose.
_RICH_CLAIM = "✅ New Owl deployed! Your agent Brain is live and will poke you every 2h."


@pytest.mark.asyncio
async def test_unverified_effect_floors_rich_claim() -> None:
    """(a) effect-classed tool verified=False + rich affirmative draft → honest floor."""
    state = _state(
        responses=(_draft(_RICH_CLAIM),),
        consequential_snapshot_taken=True,
        # owl_build claimed success but the world-read refuted it (verified=False) →
        # the snapshot lands its name here. No consequential_failures / no_progress.
        unverified_effects=("owl_build",),
    )
    result = await surface_overclaim_gate(state)
    assert result.overclaim_blocked is True
    chunk = result.responses[0]
    assert chunk.is_floor is True
    assert "deployed" not in chunk.content.lower()


@pytest.mark.asyncio
async def test_verified_effect_passes_unchanged() -> None:
    """(b) same tool verified=True → no unverified effect → draft passes UNCHANGED."""
    state = _state(
        responses=(_draft(_RICH_CLAIM),),
        consequential_snapshot_taken=True,
        # verified==True ⇒ the snapshot does NOT list owl_build → nothing to veto.
        unverified_effects=(),
        delivered_successes=("owl_build",),
    )
    result = await surface_overclaim_gate(state)
    assert result.overclaim_blocked is False
    assert result.responses[0].is_floor is False
    assert result.responses[0].content == _RICH_CLAIM


@pytest.mark.asyncio
async def test_unknown_verification_floors() -> None:
    """(c) verified=unknown (None) → default-deny → floored. unknown ≠ success."""
    state = _state(
        responses=(_draft(_RICH_CLAIM),),
        consequential_snapshot_taken=True,
        # verified is None (the world-read could not confirm) — the snapshot predicate
        # (verified is not True) still lands the name here. Burden is on PROOF.
        unverified_effects=("owl_build",),
    )
    result = await surface_overclaim_gate(state)
    assert result.overclaim_blocked is True
    assert result.responses[0].is_floor is True


@pytest.mark.asyncio
async def test_readonly_tools_normal_answer_unchanged() -> None:
    """(d) only read-only tools (no effect_class) + normal answer → unchanged."""
    state = _state(
        responses=(_draft("Here are the three files you asked about."),),
        consequential_snapshot_taken=True,
        unverified_effects=(),  # no effect-classed tool ran
    )
    result = await surface_overclaim_gate(state)
    assert result.overclaim_blocked is False
    assert result.responses[0].content == "Here are the three files you asked about."


@pytest.mark.asyncio
async def test_unverified_effect_overrides_partial_delivery() -> None:
    """An unverified effect floors even when ANOTHER effect was delivered: a turn that
    sent one message but could not prove it created the agent must not claim it exists."""
    state = _state(
        responses=(_draft(_RICH_CLAIM),),
        consequential_snapshot_taken=True,
        unverified_effects=("owl_build",),
        delivered_successes=("send_message",),  # a sibling effect DID land
    )
    result = await surface_overclaim_gate(state)
    assert result.overclaim_blocked is True
    assert result.responses[0].is_floor is True


def _effect_class_taxonomy() -> list[str]:
    """The complete set of durable effect classes any tool may declare — derived from
    ToolManifest.effect_class's Literal annotation (the single, type-enforced source of
    truth). Reading it dynamically means a NEW effect class added to the type without
    gate coverage fails this test."""

    def _flatten(ann: object) -> list[str]:
        out: list[str] = []
        for a in get_args(ann):
            if isinstance(a, str):
                out.append(a)
            else:
                out.extend(_flatten(a))
        return out

    classes = _flatten(ToolManifest.model_fields["effect_class"].annotation)
    assert classes, "could not derive the effect_class taxonomy"
    return classes


@pytest.mark.parametrize(
    ("verified", "expected"),
    [
        (True, ()),                 # a verified receipt ⇒ NOT an unverified effect
        (False, ("owl_build",)),    # claimed but refuted ⇒ unverified
        (None, ("owl_build",)),     # unknown ⇒ default-deny ⇒ unverified
    ],
)
def test_snapshot_maps_effect_verification(
    verified: bool | None, expected: tuple[str, ...]
) -> None:
    """LEDGER-FACT anchor: execute's consequential snapshot lists an effect-classed tool
    in ``unverified_effects`` iff its recorded ``verified`` is NOT True — proving
    effect_class is threaded through the turn ledger and the default-deny predicate is
    exact (unknown is treated like False, never like a success)."""
    from stackowl.infra import tool_outcome_ledger
    from stackowl.pipeline.steps.execute import _snapshot_consequential

    token = tool_outcome_ledger.bind()
    try:
        tool_outcome_ledger.record_tool_outcome(
            name="owl_build", action_severity="consequential", success=True,
            verified=verified, effect_class="creates_persistent_entity",
        )
        snap = _snapshot_consequential(_state())
    finally:
        tool_outcome_ledger.reset(token)
    assert snap.unverified_effects == expected


def test_meta_every_effect_class_is_vetoed() -> None:
    """(e) META-TEST — no effect class the veto ignores.

    Design choice (documented): the gate uses the SIMPLER "any unverified effect-classed
    tool triggers the veto" rule — there is NO per-tool / per-class claim-class map, so a
    tool cannot be 'unmapped'. Coverage is therefore over the effect_class TAXONOMY (every
    value a tool's manifest may carry, type-enforced by the Literal). For EACH class we
    assert: a tool declaring it whose result is unverified (verified is not True ⇒ the
    snapshot lists it in unverified_effects) floors an affirmative non-floor draft. A new
    effect-classed tool is covered automatically as long as it uses a declared class; a new
    class string added to the Literal without updating the gate is caught here (the gate
    keys on effect_class PRESENCE, so any string is covered — and this test enumerates the
    annotation so the taxonomy itself cannot silently grow past the gate's contract)."""
    for cls in _effect_class_taxonomy():
        # Simulate the snapshot output for a tool of this class with no verified receipt.
        state = _state(
            responses=(_draft(f"Done — your {cls} is ready!"),),
            consequential_snapshot_taken=True,
            unverified_effects=(f"tool_for_{cls}",),
        )
        is_oc, culprit = _is_overclaim(state)
        assert is_oc is True, f"effect class {cls!r} is NOT vetoed by the gate"
        assert culprit == f"tool_for_{cls}"
