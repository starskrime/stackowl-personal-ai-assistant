"""surface_overclaim_gate — block a confident non-floor response that delivered
nothing real while tools failed/bounced. STRUCTURAL (no fragile text analysis):
reuses delivered_successes (P0) + the TPS no_progress stamp. Runs AFTER the
give-up floor, BEFORE deliver, in both backends. Never raises. Emits structured
overclaim.detected / overclaim.cleared so a dead gate is visible.

PBC adds a THIRD trigger: RETRIEVAL-INTENT overclaim, the no-URL sibling of the
grounding gate. A turn whose intent required a live lookup but that ran no
``web_search``/``web_fetch`` tool is answering from the model's own (possibly
stale) knowledge with nothing to cite — floor it to the honest "I didn't
actually look this up" instead of shipping a confident guess. The classify is
lazy and gated (see ``_should_classify_retrieval``): it costs one fast-tier
one-token call, ONLY on a clean, non-delivering, non-conversational turn that
used no retrieval tool and where triggers 1/2 already cleared.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.pipeline.giveup_floor import _floor_chunk, _unrecovered_consequential_failures
from stackowl.pipeline.grounding_gate import _grounding_floor_chunk, _retrieval_ran
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState

# Trigger 3 (PBC) culprit tag — the classifier-guessed "the turn's intent needed a
# live lookup but none ran" veto, distinct from a real tool-name culprit (triggers
# 1/2) so the wrapper knows which floor prose to render.
_RETRIEVAL_CULPRIT = "retrieval"


def _is_overclaim(state: PipelineState) -> tuple[bool, str | None]:
    """Return (True, culprit) if the current draft is a structural overclaim.

    THREE triggers (an affirmative non-floor draft fires the first that holds), in
    descending confidence order — MEASURED ledger truth beats a classifier guess:

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
    3. RETRIEVAL-INTENT (PBC) — classifier-stamped, lower-confidence than 1/2 above,
       so it runs LAST and only when neither MEASURED trigger fired: the turn's
       intent required a live lookup (``state.requires_retrieval``, stamped lazily by
       the async wrapper) but no retrieval tool ran this turn. The affirmative draft
       is then answering from the model's own (possibly stale) knowledge with no URL
       to inspect — the no-URL sibling of the grounding gate.

    The empty-draft and already-floor guards clear all three. A pure conversational/
    clarify turn (no effect-classed tool, no failures, no no_progress_tools, no
    retrieval-intent stamp) is CLEARED.
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
    if culprit is not None:
        return (True, culprit)
    # Trigger 3 — RETRIEVAL-INTENT overclaim (classifier-stamped, lower-confidence
    # than the MEASURED triggers above, so it runs LAST and only on a clean,
    # non-delivering turn). The turn's intent required a live lookup but no
    # retrieval tool ran, so the affirmative draft is answering from the model's
    # own (stale) knowledge.
    if state.requires_retrieval and not _retrieval_ran(state):
        return (True, _RETRIEVAL_CULPRIT)
    return (False, None)


def _should_classify_retrieval(state: PipelineState) -> bool:
    """Cheap structural precondition (PBC Q3) gating the ONE classifier call.

    Confines the cost to the exact suspicious set: a non-empty, non-floored
    affirmative draft, on a non-conversational turn (the router already judged a
    ``conversational`` turn fully answerable from the model's own knowledge), that
    used no retrieval tool, and delivered nothing measurable. Any turn failing
    this precondition never pays for a classify call.
    """
    if not state.responses or all(not c.content.strip() for c in state.responses):
        return False
    if any(getattr(c, "is_floor", False) for c in state.responses):
        return False
    if state.intent_class == "conversational":
        return False
    if _retrieval_ran(state):
        return False
    return not state.delivered_successes


async def _stamp_requires_retrieval(state: PipelineState) -> PipelineState:
    """Lazily classify + stamp ``state.requires_retrieval`` (PBC Q2).

    Reads the classifier off ``get_services()`` — ``None`` (unwired) is a no-op so
    ``requires_retrieval`` stays at its byte-identical ``False`` default. Never
    raises: the classifier itself is fail-safe (-> False on every degraded path).
    """
    classifier = get_services().retrieval_intent_classifier
    if classifier is None:
        return state
    lookup = await classifier.requires_lookup(request=state.input_text)
    return state.evolve(requires_retrieval=lookup)


async def surface_overclaim_gate(state: PipelineState) -> PipelineState:
    """Replace a confident overclaim draft with an honest floor.

    Called AFTER surface_consequential_giveup_floor and BEFORE persist_turn /
    deliver in both backends. Never raises — any internal error is logged and the
    original state is returned unchanged (fail-open: no silent suppression of a
    valid response).

    Trigger 3 (PBC) adds ONE lazy classifier call: ``_is_overclaim`` is evaluated
    first with the default ``requires_retrieval=False`` (triggers 1/2 are free,
    MEASURED checks); only when it clears AND the Q3 precondition holds does the
    wrapper spend a single fast one-token call to stamp ``requires_retrieval``
    before re-evaluating. A turn that already overclaimed via trigger 1/2, or that
    fails the precondition (conversational, retrieved, delivered, empty/floored),
    never reaches the classifier.
    """
    try:
        is_oc, culprit = _is_overclaim(state)
        if not is_oc and _should_classify_retrieval(state):
            state = await _stamp_requires_retrieval(state)
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
        floor = (
            _grounding_floor_chunk(state)
            if culprit == _RETRIEVAL_CULPRIT
            else _floor_chunk(state, culprit)
        )
        return state.evolve(responses=(floor,), overclaim_blocked=True)
    except Exception as exc:
        log.engine.error(
            "[overclaim_gate] internal error — leaving response untouched",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state
