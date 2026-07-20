"""Unit tests for surface_overclaim_gate (Task 6 — Turn Progress Supervisor).

Covers the structural overclaim delivery-gate: a confident non-floor draft that
delivered nothing while a tool failed/bounced must be replaced with the honest
floor.  Runs AFTER surface_consequential_giveup_floor in both backends.
"""

from __future__ import annotations

import asyncio
import time
from typing import get_args

import pytest

from stackowl.pipeline.delivery_gate import (
    _is_overclaim,
    _should_classify_retrieval,
    _should_classify_schedule_commit,
    surface_overclaim_gate,
)
from stackowl.pipeline.services import (
    StepServices,
    reset_services,
    set_services,
)
from stackowl.pipeline.state import PipelineState, ToolCall
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


# ---------------------------------------------------------------------------
# PBC — Trigger 3: RETRIEVAL-INTENT overclaim (classifier-stamped, no-URL
# sibling of the grounding gate)
# ---------------------------------------------------------------------------


def _tool_call(name: str) -> ToolCall:
    return ToolCall(tool_name=name, args={}, result="ok", error=None, duration_ms=1.0)


def test_retrieval_intent_fires() -> None:
    """requires_retrieval=True + no retrieval tool ran + clean turn → floored."""
    state = _state(
        responses=(_draft("iOS 17 is the latest version."),),
        requires_retrieval=True,
        delivered_successes=(),
        consequential_failures=(),
        unverified_effects=(),
        no_progress_tools=(),
    )
    is_oc, culprit = _is_overclaim(state)
    assert is_oc is True
    assert culprit == "retrieval"


def test_retrieval_ran_not_blocked() -> None:
    """requires_retrieval=True but a retrieval tool DID run → not an overclaim."""
    state = _state(
        responses=(_draft("iOS 18 is the latest version."),),
        requires_retrieval=True,
        tool_calls=(_tool_call("web_search"),),
        delivered_successes=(),
    )
    is_oc, culprit = _is_overclaim(state)
    assert (is_oc, culprit) == (False, None)


def test_knowledge_answer_not_blocked() -> None:
    """requires_retrieval=False (default) + no tools → knowledge answer, CLEARED."""
    state = _state(
        responses=(_draft("2 + 2 = 4."),),
        delivered_successes=(),
    )
    is_oc, culprit = _is_overclaim(state)
    assert (is_oc, culprit) == (False, None)


def test_retrieval_intent_does_not_nuke_real_delivery() -> None:
    """requires_retrieval=True but something WAS delivered → delivered_successes
    early-return wins; the classifier guess never nukes a real delivery."""
    state = _state(
        responses=(_draft("Sent your report and here's today's news."),),
        requires_retrieval=True,
        delivered_successes=("send_message",),
    )
    is_oc, culprit = _is_overclaim(state)
    assert (is_oc, culprit) == (False, None)


def test_retrieval_intent_already_floored_cleared() -> None:
    """Already-floored draft + requires_retrieval=True → cleared (no double floor)."""
    state = _state(
        responses=(_draft("I wasn't able to complete that.", is_floor=True),),
        requires_retrieval=True,
        delivered_successes=(),
    )
    is_oc, culprit = _is_overclaim(state)
    assert (is_oc, culprit) == (False, None)


def test_measured_trigger_wins_over_retrieval_guess() -> None:
    """unverified_effects non-empty AND requires_retrieval=True → the MEASURED
    trigger (1) wins; the classifier guess (3) never overrides it."""
    state = _state(
        responses=(_draft("✅ Cronjob scheduled and here's the latest news."),),
        consequential_snapshot_taken=True,
        unverified_effects=("cronjob",),
        requires_retrieval=True,
    )
    is_oc, culprit = _is_overclaim(state)
    assert (is_oc, culprit) == (True, "cronjob")


class _FakeRetrievalClassifier:
    """Records calls; returns a scripted verdict. Mirrors the real classifier's
    ``requires_lookup(*, request: str) -> bool`` signature."""

    def __init__(self, verdict: bool) -> None:
        self._verdict = verdict
        self.calls: list[str] = []

    async def requires_lookup(self, *, request: str) -> bool:
        self.calls.append(request)
        return self._verdict


def _with_classifier(classifier: object | None) -> object:
    return set_services(StepServices(retrieval_intent_classifier=classifier))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_wrapper_conversational_skips_classifier() -> None:
    """A conversational-intent turn never pays for the classify call."""
    fake = _FakeRetrievalClassifier(True)
    token = _with_classifier(fake)
    try:
        state = _state(
            responses=(_draft("Sure, happy to help!"),),
            intent_class="conversational",
            delivered_successes=(),
        )
        result = await surface_overclaim_gate(state)
    finally:
        reset_services(token)
    assert fake.calls == []
    assert result.overclaim_blocked is False
    assert result.responses[0].content == "Sure, happy to help!"


@pytest.mark.asyncio
async def test_wrapper_classifies_and_floors_on_lookup_verdict() -> None:
    """Standard turn, no retrieval tool, classifier says LOOKUP → grounding floor."""
    fake = _FakeRetrievalClassifier(True)
    token = _with_classifier(fake)
    try:
        state = _state(
            responses=(_draft("The latest iOS version is 17."),),
            intent_class="standard",
            delivered_successes=(),
        )
        result = await surface_overclaim_gate(state)
    finally:
        reset_services(token)
    assert fake.calls == ["send my report"]
    assert result.overclaim_blocked is True
    chunk = result.responses[0]
    assert chunk.is_floor is True
    assert "didn't actually retrieve it" in chunk.content


@pytest.mark.asyncio
async def test_wrapper_classifier_none_is_byte_identical_noop() -> None:
    """Unwired classifier (None, default StepServices) → no floor, no error."""
    token = _with_classifier(None)
    try:
        state = _state(
            responses=(_draft("The latest iOS version is 17."),),
            intent_class="standard",
            delivered_successes=(),
        )
        result = await surface_overclaim_gate(state)
    finally:
        reset_services(token)
    assert result.overclaim_blocked is False
    assert result.responses[0].content == "The latest iOS version is 17."


def test_should_classify_retrieval_precondition() -> None:
    """Direct precondition unit coverage for the cost-gate itself."""
    conversational = _state(
        responses=(_draft("hi"),), intent_class="conversational",
    )
    assert _should_classify_retrieval(conversational) is False

    retrieved = _state(
        responses=(_draft("news"),), tool_calls=(_tool_call("web_search"),),
    )
    assert _should_classify_retrieval(retrieved) is False

    delivered = _state(
        responses=(_draft("sent"),), delivered_successes=("send_message",),
    )
    assert _should_classify_retrieval(delivered) is False

    floored = _state(responses=(_draft("x", is_floor=True),))
    assert _should_classify_retrieval(floored) is False

    empty = _state(responses=(_draft("   "),))
    assert _should_classify_retrieval(empty) is False

    suspicious = _state(responses=(_draft("iOS 17 is latest"),))
    assert _should_classify_retrieval(suspicious) is True


# ---------------------------------------------------------------------------
# Trigger 4: SCHEDULING-COMMITMENT overclaim (classifier-stamped, no-tool-call
# sibling of trigger 1's MEASURED unverified-effect check)
# ---------------------------------------------------------------------------


def test_scheduling_commit_fires() -> None:
    """requires_scheduling_commit=True + no schedules tool ran → floored."""
    state = _state(
        responses=(_draft("Sure, I'll ping you in 5 minutes!"),),
        requires_scheduling_commit=True,
        delivered_successes=(),
        consequential_failures=(),
        unverified_effects=(),
        no_progress_tools=(),
        ran_effect_classes=(),
    )
    is_oc, culprit = _is_overclaim(state)
    assert is_oc is True
    assert culprit == "scheduling_commit"


def test_scheduling_commit_schedules_tool_ran_not_blocked() -> None:
    """requires_scheduling_commit=True but a schedules-effect tool DID run this
    turn → not this trigger's job (trigger 1 already covers whether it verified)."""
    state = _state(
        responses=(_draft("Done — I've scheduled that reminder for you."),),
        requires_scheduling_commit=True,
        ran_effect_classes=("schedules",),
        unverified_effects=(),
        delivered_successes=(),
    )
    is_oc, culprit = _is_overclaim(state)
    assert (is_oc, culprit) == (False, None)


def test_no_commitment_answer_not_blocked() -> None:
    """requires_scheduling_commit=False (default) + no tools → CLEARED."""
    state = _state(
        responses=(_draft("2 + 2 = 4."),),
        delivered_successes=(),
    )
    is_oc, culprit = _is_overclaim(state)
    assert (is_oc, culprit) == (False, None)


def test_scheduling_commit_does_not_nuke_real_delivery() -> None:
    """requires_scheduling_commit=True but something WAS delivered → delivered_successes
    early-return wins; the classifier guess never nukes a real delivery."""
    state = _state(
        responses=(_draft("Sent your report, and I'll check back later too."),),
        requires_scheduling_commit=True,
        delivered_successes=("send_message",),
    )
    is_oc, culprit = _is_overclaim(state)
    assert (is_oc, culprit) == (False, None)


def test_scheduling_commit_already_floored_cleared() -> None:
    """Already-floored draft + requires_scheduling_commit=True → cleared (no double floor)."""
    state = _state(
        responses=(_draft("I wasn't able to complete that.", is_floor=True),),
        requires_scheduling_commit=True,
        delivered_successes=(),
    )
    is_oc, culprit = _is_overclaim(state)
    assert (is_oc, culprit) == (False, None)


def test_measured_trigger_wins_over_scheduling_commit_guess() -> None:
    """unverified_effects non-empty AND requires_scheduling_commit=True → the MEASURED
    trigger (1) wins; the classifier guess (4) never overrides it."""
    state = _state(
        responses=(_draft("✅ Cronjob scheduled and I'll also ping you later."),),
        consequential_snapshot_taken=True,
        unverified_effects=("cronjob",),
        requires_scheduling_commit=True,
    )
    is_oc, culprit = _is_overclaim(state)
    assert (is_oc, culprit) == (True, "cronjob")


class _FakeScheduleCommitClassifier:
    """Records calls; returns a scripted verdict. Mirrors the real classifier's
    ``commits_to_future_schedule(*, response: str) -> bool`` signature."""

    def __init__(self, verdict: bool) -> None:
        self._verdict = verdict
        self.calls: list[str] = []

    async def commits_to_future_schedule(self, *, response: str) -> bool:
        self.calls.append(response)
        return self._verdict


def _with_schedule_classifier(classifier: object | None) -> object:
    return set_services(StepServices(schedule_commit_classifier=classifier))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_wrapper_conversational_turn_still_pays_for_schedule_classifier() -> None:
    """Unlike trigger 3, a conversational-intent turn is NOT skipped — this is
    exactly the shape of the bug (a casual 'sure I'll ping you' chat reply)."""
    fake = _FakeScheduleCommitClassifier(True)
    token = _with_schedule_classifier(fake)
    try:
        state = _state(
            responses=(_draft("Sure, I'll ping you in five!"),),
            intent_class="conversational",
            delivered_successes=(),
        )
        result = await surface_overclaim_gate(state)
    finally:
        reset_services(token)
    assert fake.calls == ["Sure, I'll ping you in five!"]
    assert result.overclaim_blocked is True
    chunk = result.responses[0]
    assert chunk.is_floor is True
    assert "didn't actually schedule" in chunk.content


@pytest.mark.asyncio
async def test_wrapper_classifies_and_floors_on_commit_verdict() -> None:
    """Standard turn, no schedules tool, classifier says COMMIT → schedule floor."""
    fake = _FakeScheduleCommitClassifier(True)
    token = _with_schedule_classifier(fake)
    try:
        state = _state(
            responses=(_draft("I'll check your website every 2 hours and let you know."),),
            intent_class="standard",
            delivered_successes=(),
        )
        result = await surface_overclaim_gate(state)
    finally:
        reset_services(token)
    assert result.overclaim_blocked is True
    chunk = result.responses[0]
    assert chunk.is_floor is True
    assert "didn't actually schedule" in chunk.content


@pytest.mark.asyncio
async def test_wrapper_schedule_classifier_none_is_byte_identical_noop() -> None:
    """Unwired classifier (None, default StepServices) → no floor, no error."""
    token = _with_schedule_classifier(None)
    try:
        state = _state(
            responses=(_draft("I'll ping you in five minutes."),),
            intent_class="standard",
            delivered_successes=(),
        )
        result = await surface_overclaim_gate(state)
    finally:
        reset_services(token)
    assert result.overclaim_blocked is False
    assert result.responses[0].content == "I'll ping you in five minutes."


def test_should_classify_schedule_commit_precondition() -> None:
    """Direct precondition unit coverage for the cost-gate itself."""
    # Unlike retrieval's precondition, a conversational turn is NOT excluded —
    # that's exactly the shape of the target bug.
    conversational = _state(
        responses=(_draft("sure, I'll remind you"),), intent_class="conversational",
    )
    assert _should_classify_schedule_commit(conversational) is True

    schedules_ran = _state(
        responses=(_draft("scheduled it"),), ran_effect_classes=("schedules",),
    )
    assert _should_classify_schedule_commit(schedules_ran) is False

    floored = _state(responses=(_draft("x", is_floor=True),))
    assert _should_classify_schedule_commit(floored) is False

    empty = _state(responses=(_draft("   "),))
    assert _should_classify_schedule_commit(empty) is False

    suspicious = _state(responses=(_draft("I'll ping you in 5 minutes"),))
    assert _should_classify_schedule_commit(suspicious) is True


def test_snapshot_excludes_no_side_effect_from_unverified() -> None:
    """A call that never attempted its effect (side_effect_committed=False) must not
    land in unverified_effects — there is nothing to demand proof of. Mirrors
    is_effectful_failure's own `or not side_effect_committed` guard, which this
    predicate previously lacked (a real live bug: cronjob's read-only 'list' action
    shares the tool's effect_class="schedules" manifest, and with no override its
    ToolResult defaulted side_effect_committed=True, so verify()'s honest "no
    opinion" (None) on list floored the very next affirmative answer)."""
    from stackowl.infra import tool_outcome_ledger
    from stackowl.pipeline.steps.execute import _snapshot_consequential

    token = tool_outcome_ledger.bind()
    try:
        tool_outcome_ledger.record_tool_outcome(
            name="cronjob", action_severity="write", success=True,
            side_effect_committed=False, verified=None, effect_class="schedules",
        )
        snap = _snapshot_consequential(_state())
    finally:
        tool_outcome_ledger.reset(token)
    assert snap.unverified_effects == ()


@pytest.mark.parametrize(
    ("outcome_effect_class", "expected"),
    [
        ("schedules", ("schedules",)),
        (None, ()),
    ],
)
def test_snapshot_maps_ran_effect_classes(
    outcome_effect_class: str | None, expected: tuple[str, ...]
) -> None:
    """LEDGER-FACT anchor: execute's consequential snapshot lists a tool's
    effect_class in ran_effect_classes iff it declared one — regardless of
    verified/success — proving trigger 4 reads "did a schedules tool run AT
    ALL", distinct from unverified_effects' "did it prove itself"."""
    from stackowl.infra import tool_outcome_ledger
    from stackowl.pipeline.steps.execute import _snapshot_consequential

    token = tool_outcome_ledger.bind()
    try:
        tool_outcome_ledger.record_tool_outcome(
            name="cronjob", action_severity="write", success=True,
            verified=True, effect_class=outcome_effect_class,
        )
        snap = _snapshot_consequential(_state())
    finally:
        tool_outcome_ledger.reset(token)
    assert snap.ran_effect_classes == expected


# ---------------------------------------------------------------------------
# LAT — trigger 3 (retrieval) + trigger 4 (schedule-commit) run CONCURRENTLY
# ---------------------------------------------------------------------------


class _SlowRetrievalClassifier:
    """Fake retrieval classifier that sleeps ``delay`` before returning ``verdict``.
    Used to prove the two independent classifier calls overlap in wall-clock."""

    def __init__(self, delay: float, verdict: bool = False) -> None:
        self._delay = delay
        self._verdict = verdict

    async def requires_lookup(self, *, request: str) -> bool:
        await asyncio.sleep(self._delay)
        return self._verdict


class _SlowScheduleClassifier:
    def __init__(self, delay: float, verdict: bool = False) -> None:
        self._delay = delay
        self._verdict = verdict

    async def commits_to_future_schedule(self, *, response: str) -> bool:
        await asyncio.sleep(self._delay)
        return self._verdict


@pytest.mark.asyncio
async def test_trigger3_and_trigger4_classifiers_run_concurrently() -> None:
    """Both preconditions hold → the two independent fast classifiers run under one
    asyncio.gather, so total wall-clock is ~max(delay), not the serial sum(2*delay)."""
    delay = 0.25
    ric = _SlowRetrievalClassifier(delay)
    scc = _SlowScheduleClassifier(delay)
    token = set_services(
        StepServices(  # type: ignore[arg-type]
            retrieval_intent_classifier=ric,
            schedule_commit_classifier=scc,
        )
    )
    try:
        # A standard turn, non-floor non-empty draft, no retrieval/schedule tool ran,
        # delivered nothing → BOTH trigger preconditions are satisfied.
        state = _state(
            responses=(_draft("The latest iOS version is 17 and I'll ping you later."),),
            intent_class="standard",
            delivered_successes=(),
        )
        assert _should_classify_retrieval(state) is True
        assert _should_classify_schedule_commit(state) is True
        t0 = time.perf_counter()
        result = await surface_overclaim_gate(state)
        elapsed = time.perf_counter() - t0
    finally:
        reset_services(token)
    # Both verdicts False → no overclaim floor.
    assert result.overclaim_blocked is False
    # Concurrent: close to one delay, comfortably under the serial 2*delay sum.
    assert elapsed < delay * 1.8, f"expected concurrent (~{delay}s), got {elapsed:.3f}s (serial?)"


# ---------------------------------------------------------------------------
# Trigger 4 fulfillment — do-the-action upgrade: a detected scheduling promise
# is FULFILLED (job minted via the fulfiller) instead of confessed; the honest
# ask-floor remains the fallback when fulfillment fails.
# ---------------------------------------------------------------------------


class _FakeFulfiller:
    def __init__(self, receipt: str | None) -> None:
        self._receipt = receipt
        self.calls: list[tuple[str, str]] = []

    async def fulfill(self, *, response: str, request: str) -> str | None:
        self.calls.append((response, request))
        return self._receipt


@pytest.mark.asyncio
async def test_scheduling_commit_fulfilled_keeps_answer_and_appends_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeFulfiller("\n\n📅 Scheduled: ping the user (in 5m)")

    async def _fake_try(state):  # noqa: ANN001, ANN202
        return await fake.fulfill(response="r", request=state.input_text)

    import stackowl.pipeline.delivery_gate as gate_mod

    monkeypatch.setattr(gate_mod, "_try_fulfill_schedule_commit", _fake_try)
    state = _state(
        responses=(_draft("Sure, I'll ping you in 5 minutes!"),),
        requires_scheduling_commit=True,
        delivered_successes=(),
        unverified_effects=(),
        ran_effect_classes=(),
    )
    result = await surface_overclaim_gate(state)
    # Original answer preserved, receipt appended, NOT blocked, NOT floored.
    assert result.overclaim_blocked is False
    texts = [c.content for c in result.responses]
    assert texts[0] == "Sure, I'll ping you in 5 minutes!"
    assert "📅 Scheduled" in texts[-1]
    assert not any(getattr(c, "is_floor", False) for c in result.responses)
    assert fake.calls, "fulfiller was never consulted"


@pytest.mark.asyncio
async def test_scheduling_commit_fulfillment_failure_falls_back_to_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_try(state):  # noqa: ANN001, ANN202
        return None  # every degraded path collapses to None

    import stackowl.pipeline.delivery_gate as gate_mod

    monkeypatch.setattr(gate_mod, "_try_fulfill_schedule_commit", _fake_try)
    state = _state(
        responses=(_draft("Sure, I'll ping you in 5 minutes!"),),
        requires_scheduling_commit=True,
        delivered_successes=(),
        unverified_effects=(),
        ran_effect_classes=(),
    )
    result = await surface_overclaim_gate(state)
    # Unchanged legacy behavior: the honest ask-floor replaces the draft.
    assert result.overclaim_blocked is True
    assert len(result.responses) == 1
    assert result.responses[0].is_floor is True
    assert "didn't actually schedule" in result.responses[0].content
