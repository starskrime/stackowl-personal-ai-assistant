"""Shared backend-agnostic post-run seam (FR-11/FR-12).

Both ``AsyncioBackend`` and ``LangGraphBackend`` call into this module instead of
each carrying its own copy of the post-execute surfacing sequence, acceptance
verification, outcome capture, and skill success-rate update. Mechanical
consolidation only — the eight surfacers ``run_delivery_gate`` calls in sequence
(applied_lessons, recovery, persistence_handoff, giveup_floor, overclaim_gate,
grounding_gate, critical_failure, command_hint) are unchanged; this seam calls
them in the exact order each backend already did. Of those, giveup_floor,
overclaim_gate, grounding_gate, critical_failure, and persistence_handoff are
the five honesty-critical gate modules FR-11 targets for a later, separate
physical merge into one file — untouched here, only their call site moved.

**Workstream C item 5** — ``bind_turn_context``/``unbind_turn_context`` below
consolidate the ~90-110 lines of near-byte-identical ContextVar bind/reset
boilerplate each backend's ``run()`` used to carry independently (turn-scoped
TraceContext/lesson/recovery/retry-ledger/tool-outcome-ledger/decision-ledger/
human-wait binding, and their mirrored teardown + summary logging in a
``finally``). Deliberately EXCLUDES the live-progress-callback bind
(``bind_progress_callback``/``emit_progress_start``) — that is genuinely
asyncio-only (LangGraph has no live-progress wiring at all today), a real
specialization, not duplication, so it stays in ``AsyncioBackend.run()``
itself, bracketing a call to these shared functions rather than being forced
into them.
"""

from __future__ import annotations

import time
from contextvars import Token
from dataclasses import dataclass

from stackowl.infra import decision_ledger, recovery_context, retry_ledger, tool_outcome_ledger
from stackowl.infra.observability import log
from stackowl.infra.tool_outcome_ledger import ToolOutcome
from stackowl.infra.trace import TraceContext, _TraceToken
from stackowl.memory.outcome_store import TaskOutcomeStore, classify_failure
from stackowl.objectives.model import ExpectedOutcome
from stackowl.pipeline import lesson_context as lc
from stackowl.pipeline.acceptance import AcceptanceChecker, AcceptanceVerdict
from stackowl.pipeline.applied_lessons import surface_applied_lessons
from stackowl.pipeline.budget import human_wait as human_wait_ctx
from stackowl.pipeline.command_hint import surface_command_hint
from stackowl.pipeline.delivery_gate import (
    surface_consequential_giveup_floor,
    surface_critical_failure,
    surface_grounding_gate,
    surface_overclaim_gate,
    surface_persistence_handoff,
)
from stackowl.pipeline.lesson_context import _LessonToken
from stackowl.pipeline.recovery_summary import surface_recovery
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.turn_persist import persist_turn


@dataclass(frozen=True, slots=True)
class TurnBindings:
    """Reset tokens + timing captured by :func:`bind_turn_context`.

    Opaque to callers beyond passing it straight to
    :func:`unbind_turn_context` — field names mirror each ContextVar's own
    ``bind()``/``reset()`` pair.
    """

    services_token: Token[StepServices]
    trace_token: _TraceToken
    lesson_token: _LessonToken
    recovery_token: Token[list[recovery_context.RecoveryEvent] | None]
    retry_ledger_token: Token[list[retry_ledger.RetryEvent] | None]
    tool_outcome_token: Token[tuple[ToolOutcome, ...] | None]
    decision_token: Token[list[decision_ledger.Decision] | None] | None
    human_wait_token: Token[float]
    t0: float
    wall_t0: float
    deadline_s: float


def bind_turn_context(state: PipelineState, services: StepServices) -> TurnBindings:
    """Install every turn-scoped ContextVar for one ``run()`` call.

    Order matches what both backends' ``run()`` did inline before this
    consolidation: services → TraceContext → lesson_context → recovery_context
    → retry_ledger → tool_outcome_ledger → decision_ledger (conditional on the
    ``decision_ledger`` setting, default ON) → human_wait. ``deadline_s`` is
    computed here too (a pure derivation from ``state.interactive`` +
    ``services.settings``) since both backends immediately needed it right
    after binding.
    """
    t0 = time.monotonic()
    # Wall-clock turn start — the acceptance freshness clock (a fresh artifact's
    # mtime is compared against this). monotonic() is unsuitable (not epoch).
    wall_t0 = time.time()
    services_token = set_services(services)
    trace_token = TraceContext.start(
        state.session_id,
        trace_id=state.trace_id,
        interactive=state.interactive,
        channel=state.channel,
        reply_target=state.reply_target,
        delegation_depth=state.delegation_depth,
        delegation_chain=state.delegation_chain,
        delegation_profile=state.delegation_profile,
        owl_name=state.owl_name,
        creation_ceiling=state.creation_ceiling,
        task_id=state.task_id,
        durable_owner_id=state.durable_owner_id,
        retry_lineage_id=state.retry_lineage_id,
    )
    lesson_token = lc.bind()
    recovery_token = recovery_context.bind()
    retry_ledger_token = retry_ledger.bind()
    tool_outcome_token = tool_outcome_ledger.bind()
    # ADR-7 — bind the per-turn DecisionLedger only when enabled (default ON; off
    # only if settings explicitly sets decision_ledger=False). Unbound ⇒
    # record_decision no-ops ⇒ byte-identical to the pre-ADR-7 path.
    _settings = services.settings
    decision_token = (
        decision_ledger.bind()
        if _settings is None or _settings.decision_ledger
        else None
    )
    human_wait_token = human_wait_ctx.bind()
    # Global interactive turn deadline (2026-07 incident: a telegram turn hung
    # 1670+s — lower-level timeouts like resilient_round don't cover every
    # hang, e.g. a wedged tool call). Interactive turns only; the long-running
    # non-interactive paths (goal_execution, parliament, delegation children,
    # evolution) carry their own budgets and must never be cut by this.
    # getattr-guarded: unit tests hand StepServices duck-typed settings stubs
    # without a `system` section — those (and settings=None) mean "deadline
    # disabled", never a crash.
    deadline_s: float = (
        getattr(getattr(_settings, "system", None), "interactive_turn_timeout_s", 0.0)
        if state.interactive
        else 0.0
    )
    return TurnBindings(
        services_token=services_token, trace_token=trace_token, lesson_token=lesson_token,
        recovery_token=recovery_token, retry_ledger_token=retry_ledger_token,
        tool_outcome_token=tool_outcome_token, decision_token=decision_token,
        human_wait_token=human_wait_token, t0=t0, wall_t0=wall_t0, deadline_s=deadline_s,
    )


async def unbind_turn_context(
    bindings: TurnBindings, state: PipelineState, services: StepServices, *, backend_name: str,
) -> int:
    """Tear down every ContextVar :func:`bind_turn_context` installed.

    Call from the backend's own ``finally`` — mirrors the bind order in
    reverse-ish (matches what both backends did inline: recovery summary log
    → human_wait reset → decision persist-then-reset → tool_outcome reset →
    recovery reset → retry summary log + reset → lesson reset → TraceContext
    reset → services reset). Returns ``retry_event_count`` — the number of
    provider-layer retry events this turn recorded — captured here (while
    retry_ledger is still bound) for the caller to thread into
    ``_capture_outcome`` afterward, since that runs after this function
    returns and the ledger is reset (see :func:`_capture_outcome`'s
    docstring).
    """
    _rec_events = recovery_context.get_recovery()
    if _rec_events:
        log.engine.info(
            "[recovery] turn summary",
            extra={"_fields": {
                "trace_id": state.trace_id,
                "events": [
                    {"kind": e.kind, "failed": e.failed,
                     "recovered_via": e.recovered_via, "user_visible": e.user_visible}
                    for e in _rec_events
                ],
            }},
        )
    human_wait_ctx.reset(bindings.human_wait_token)
    if bindings.decision_token is not None:
        # ADR-7 — persist this turn's decisions durably (cross-process /
        # restart-safe) BEFORE reset clears the ledger. Best-effort: a
        # persistence failure must NEVER break the turn (B5).
        _decisions = decision_ledger.get_decisions()
        if services.db_pool is not None and state.session_id and _decisions:
            try:
                from stackowl.pipeline.decision_store import TurnDecisionStore
                await TurnDecisionStore(services.db_pool).save(
                    session_id=state.session_id,
                    trace_id=state.trace_id,
                    decisions=_decisions,
                )
            except Exception as exc:
                log.engine.error(
                    f"[{backend_name}] run: decision persist failed (swallowed)",
                    exc_info=exc,
                    extra={"_fields": {"session_id": state.session_id}},
                )
        decision_ledger.reset(bindings.decision_token)
    tool_outcome_ledger.reset(bindings.tool_outcome_token)
    recovery_context.reset(bindings.recovery_token)
    _retry_events = retry_ledger.get_retry()
    if _retry_events:
        log.engine.info(
            "[retry] turn summary",
            extra={"_fields": {
                "trace_id": state.trace_id,
                "retry_lineage_id": state.retry_lineage_id,
                "events": [
                    {"kind": e.kind, "provider": e.provider, "detail": e.detail,
                     "attempt_number": e.attempt_number}
                    for e in _retry_events
                ],
            }},
        )
    retry_event_count = len(_retry_events)
    retry_ledger.reset(bindings.retry_ledger_token)
    lc.reset(bindings.lesson_token)
    TraceContext.reset(bindings.trace_token)
    reset_services(bindings.services_token)
    return retry_event_count

# B4b — the general failure_class stamped on a turn whose only "success" was an
# UNRECOVERED effectful failure the error-based classifier missed (a verified=False
# false win, or a success=False effectful tool that never raised). Keys the
# positive-only learner OFF the turn so a false win is never mined as a win. General
# and vendor-neutral — names the SHAPE of the failure, not any tool/site.
_UNACHIEVED_EFFECT_CLASS = "unachieved_effect"

# LS7 — how hard one turn's MEASURED outcome nudges a skill's success_rate. EWMA
# so a single bad turn corrects rather than overwrites accumulated history.
_SKILL_SUCCESS_EWMA_ALPHA = 0.3


async def _update_skill_success_rates(
    services: StepServices, state: PipelineState, *, success: bool,
) -> None:
    """Feed the turn's MEASURED outcome into the success_rate of every skill the
    model APPLIED this turn — the read side that revives the synthesizer's
    refine/deprecate gates (they read success_rate + n_executions).

    The application seam is the ``skill_view`` tool calls recorded this turn (the
    model pulled the playbook to apply it) — NOT prompt injection: an injected-but-
    unloaded skill leaves no skill_view call, so it never moves. EWMA blend with the
    prior rate (seed on first sample). Best-effort (B5): a stats-write error must
    never crash the turn.
    """
    store = services.skill_store
    if store is None:
        return
    viewed: list[str] = []
    for tc in state.tool_calls:
        if tc.tool_name != "skill_view":
            continue
        raw = str(tc.args.get("name", "")).strip()
        # Strip a 'source:' qualifier to a bare name for store resolution.
        bare = raw.partition(":")[2].strip() if ":" in raw else raw
        if bare:
            viewed.append(bare)
    if not viewed:
        return
    try:
        skills = await store.get_many_by_name(tuple(dict.fromkeys(viewed)))
        outcome = 1.0 if success else 0.0
        for sk in skills:
            new_rate = outcome if sk.success_rate is None else (
                _SKILL_SUCCESS_EWMA_ALPHA * outcome
                + (1.0 - _SKILL_SUCCESS_EWMA_ALPHA) * sk.success_rate
            )
            await store.set_success_rate(sk.skill_id, new_rate)
        log.engine.debug(
            "[outcomes] skill success_rate nudged",
            extra={"_fields": {
                "trace_id": state.trace_id, "success": success, "skills": len(skills),
            }},
        )
    except Exception as exc:  # B5 — telemetry must never crash the turn
        log.engine.warning(
            "[outcomes] skill success_rate update failed",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )


async def run_delivery_gate(current: PipelineState, services: StepServices) -> PipelineState:
    """The single post-execute gate cascade + persist_turn sequence shared by both
    backends (FR-11/FR-12). Same 8 calls, same order, as each backend ran inline
    before this consolidation.
    """
    # Applied-lesson annotation runs BEFORE critical-failure surfacing: on a
    # failed turn there is no real answer yet, so the honesty guard suppresses
    # the note; on a success turn the answer is present and gets annotated,
    # after which critical-failure surfacing no-ops. Order matters — see the
    # learning-explainability journey's critical-failure test.
    current = await surface_applied_lessons(current)
    current = await surface_recovery(current)
    # Judge-independent gate: if a consequential/write action failed with no
    # success, REPLACE the (potentially dressed-up) draft with an honest floor
    # naming the failed capability. Runs BEFORE surface_critical_failure so the
    # critical-failure cascade sees an honest state (never hides behind a giveup).
    # Never-give-up rung (PA4): a turn that would give up first tries to hand
    # the whole request to a better-fit owl and deliver ITS answer. A failed
    # hand-off leaves responses untouched → the honest floor below still fires.
    current = await surface_persistence_handoff(current, services)
    current = await surface_consequential_giveup_floor(current)
    # Overclaim delivery-gate (Task 6): if the draft is confident but nothing
    # was delivered while a tool failed/bounced, replace it with the honest
    # floor. Structural — reads ledger state, not response text. Never raises.
    current = await surface_overclaim_gate(current)
    # Grounding gate (ADR-T3 / TS5+TS6): strip fabricated citations (URLs the
    # turn never retrieved) and floor an ungrounded external-info answer.
    # Keyed on URLs + the retrieval ledger, never the prose. Never raises.
    current = await surface_grounding_gate(current)
    # Phase 2 #2 — surface a CRITICAL (execute) step failure to the user
    # BEFORE deliver, so silence is replaced by a localized apology. Shared
    # with LangGraphBackend; self-healing (never raises into the backend).
    current = await surface_critical_failure(current, services)
    # WS-D issue 3 — additively append a marked NL→command hint (and any
    # routing-correction notice) to a REAL answer. Runs AFTER the honesty
    # floors so it never decorates a floored/failed turn; gated by
    # ui.command_hints (no-op + byte-identical when off). Never raises.
    current = await surface_command_hint(current, services)

    # F088 — persist the turn AFTER the honest floor band, synchronously
    # inside the ledger ContextVar binding (persist_turn reads it). On a
    # floored turn this records the user utterance only, never the
    # dressed-up draft — so the dream worker never promotes a lie. Relocated
    # out of consolidate (which used to persist the PRE-floor draft).
    await persist_turn(current)
    return current


async def _verify_turn_acceptance(
    state: PipelineState, turn_started_at: float, services: StepServices,
) -> AcceptanceVerdict | None:
    """Observe this turn's DECLARED/DERIVED acceptance post-condition. Never raises.

    Returns ``None`` (no opinion) when no outcome is declared on ``state`` and the
    flag-OFF LLM-derived layer yields nothing — the default normal turn, kept
    byte-identical. A declared (or derived) outcome is checked by the same
    :class:`AcceptanceChecker` the objectives driver uses, mirroring its ``acted``
    gate (a pure no-op turn is never penalized for an outcome it could not produce).
    """
    try:
        criteria = state.expected_outcome
        if criteria is None:
            criteria = await _derive_turn_acceptance(state, services)
        if criteria is None:
            return None
        verdict = AcceptanceChecker().check(
            criteria,
            turn_started_at=turn_started_at,
            acted=bool(state.responses or state.tool_calls),
        )
        if verdict.accepted is not None:
            log.engine.info(
                "[acceptance] normal-turn verdict",
                extra={"_fields": {
                    "trace_id": state.trace_id,
                    "accepted": verdict.accepted,
                    "reason": verdict.reason[:160],
                }},
            )
        return verdict
    except Exception as exc:  # never let acceptance sink the turn's outcome capture
        log.engine.warning(
            "[acceptance] normal-turn check raised — no opinion",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return None


async def _derive_turn_acceptance(
    state: PipelineState, services: StepServices,
) -> ExpectedOutcome | None:
    """OPTIONAL post-hoc LLM-derived acceptance (flag-OFF default). Mirrors
    ``ObjectiveDriverHandler._derive_acceptance``: returns a derived ExpectedOutcome
    ONLY when ``settings.acceptance_tier`` is set AND a provider registry is wired,
    fail-CLOSED (the deriver returns None on any model error). None on every default
    path ⇒ byte-identical."""
    settings = services.settings
    tier = settings.acceptance_tier if settings is not None else ""
    if not tier or services.provider_registry is None:
        return None
    from stackowl.pipeline.acceptance_llm import LlmAcceptanceDeriver

    # "".join, not "\n".join (retry_actuator.py rule): streamed = one chunk per
    # token; a newline join hands the deriver a corrupted one-word-per-line draft.
    response_text = "".join(c.content for c in state.responses if c.content)
    if not response_text.strip():
        return None
    deriver = LlmAcceptanceDeriver(services.provider_registry, tier)
    return await deriver.derive(intent=state.input_text, draft=response_text)


async def _capture_outcome(
    state: PipelineState, total_ms: float, services: StepServices,
    *, acceptance: AcceptanceVerdict | None = None, retry_event_count: int = 0,
) -> None:
    """Persist a row in task_outcomes for this run. Best-effort — logs on failure.

    Captures: success (no errors), latency_ms, tool_call_count, failure_class
    (from state.errors via classify_failure), step_durations, input_text,
    response_text. quality_score / scored_at start NULL — the CriticScorerHandler
    fills them in asynchronously later.

    ``acceptance`` (F-11) is the normal-turn goal-level verdict. ``accepted is False``
    is an UNACHIEVED EFFECT — the turn claimed an outcome reality refuted — so the
    row is marked not-trustworthy and labelled so the positive-only learner skips it,
    exactly mirroring the unrecovered-effect path. ``None`` (the default) is a no-op.

    ``retry_event_count`` (Workstream B, Phase 5) — how many provider-layer
    retry/circuit-breaker events fired during this turn. MUST be passed in by
    the caller, captured from retry_ledger.get_retry() BEFORE that backend's
    own reset — reading the ContextVar directly HERE would always see ()
    (this function runs after both backends' own retry_ledger.reset(), the
    same lesson already learned once for recovery_context; see
    recovered_via_tool's derivation below for that original fix).
    """
    # 1. ENTRY
    log.engine.debug(
        "[outcomes] capture: entry",
        extra={"_fields": {
            "trace_id": state.trace_id,
            "total_ms": total_ms,
            "error_count": len(state.errors),
            "tool_call_count": len(state.tool_calls),
        }},
    )
    # 2. DECISION — services may not have a db pool (tests / dry-run / degraded)
    db = services.db_pool
    if db is None:
        log.engine.debug(
            "[outcomes] capture: exit — no db_pool, skipped",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return
    # 3. STEP — derive payload and persist
    try:
        store = TaskOutcomeStore(db)
        # "".join (retry_actuator.py rule) — this row feeds the critic scorer,
        # DNA attribution, and shadow-validator replays; a newline-per-token
        # response_text corrupts every downstream learner.
        response_text = "".join(c.content for c in state.responses if c.content)
        failure_class = classify_failure(state.errors)
        # B4b — make the LEARNER's signal trustworthy (verification arc). The
        # positive-only miner / critic scorer / reflection trigger all treat
        # ``failure_class IS NULL`` as "clean win". ``classify_failure`` reads
        # ``state.errors`` ALONE, so a verified=False false win (success=True, no
        # exception) — the instagram_media_extractor class — would persist as a
        # learnable WIN. The B2 snapshot already measured the truth on immutable
        # state: ``consequential_failures`` holds effects claimed-but-not-observed
        # (and effectful tools that returned success=False), while
        # ``recovered_consequential`` holds the ones the recovery actuator HEALED
        # (retry / substitution). An UNRECOVERED effectful failure with no raised
        # error is the corruption case → label it so the learner skips it. This does
        # NOT learn negatives (positive-only is unchanged); it stops MIS-learning a
        # false win as a positive.
        unrecovered_effects = (
            set(state.consequential_failures) - set(state.recovered_consequential)
        )
        # F-11 — a REFUTED goal-level acceptance verdict is an unachieved effect too
        # (the turn declared/derived a post-condition reality did not satisfy).
        acceptance_refuted = acceptance is not None and acceptance.accepted is False
        if failure_class is None and (unrecovered_effects or acceptance_refuted):
            failure_class = _UNACHIEVED_EFFECT_CLASS
        trustworthy_success = (
            len(state.errors) == 0 and not unrecovered_effects and not acceptance_refuted
        )
        # ADR-6 Task 6 fix — the FAILED tool a substitution recovery bridged THIS
        # turn (first one, if several). Stamped even on a trustworthy-SUCCESS row:
        # a bridged turn has failure_class=None and is invisible to
        # list_failed_global, so this is the only durable signal for a capability
        # recovering via substitution over and over — the "permanent fallback
        # with zero retry" masked-chronic-outage shape self-heal must catch.
        # CRITICAL: reads ``state.recovered_via_substitution`` (stamped by
        # execute._snapshot_consequential while recovery_context was still
        # bound), NEVER recovery_context.get_recovery() directly — by the time
        # this outcome-capture step runs, both backends' ``finally`` has ALREADY
        # called recovery_context.reset(), so a direct ContextVar read here would
        # silently and always return () (a prior version of this fix had exactly
        # that bug — see task-6-report.md).
        recovered_via_tool = (
            state.recovered_via_substitution[0] if state.recovered_via_substitution else None
        )
        # Migration 0078 — the ONE capability that actually failed this turn,
        # persisted so the failure-clustering miner no longer has to blame
        # every tool named in tool_sequence for a turn-level verdict (see
        # cluster_failures_by_capability_and_signature's docstring for the
        # incident this fixes). Prefer the first UNRECOVERED consequential
        # failure (order preserved from consequential_failures, filtered to
        # unrecovered_effects); fall back to the first tool_call that
        # actually raised an error (covers the classify_failure/state.errors
        # path, which unrecovered_effects doesn't cover). None when neither
        # signal is present (e.g. an acceptance-refuted turn with no failed
        # tool call) — the miner falls back to co-occurrence for those rows.
        failed_capability = next(
            (t for t in state.consequential_failures if t in unrecovered_effects), None,
        ) or next((tc.tool_name for tc in state.tool_calls if tc.error), None)
        # Snapshot DNA from the owl registry so attribution-based evolution
        # (Learning Commit 4) can correlate trait values with outcome quality.
        # Best-effort — owl may not be registered (system commands, parliament).
        dna_snapshot: dict[str, float] | None = None
        if services.owl_registry is not None:
            try:
                manifest = services.owl_registry.get(state.owl_name)
                dna = manifest.dna
                dna_snapshot = {
                    "challenge_level": float(dna.challenge_level),
                    "verbosity": float(dna.verbosity),
                    "curiosity": float(dna.curiosity),
                    "formality": float(dna.formality),
                    "creativity": float(dna.creativity),
                    "precision": float(dna.precision),
                    "completion_drive": float(dna.completion_drive),
                }
            except Exception as exc:  # B5
                log.engine.debug(
                    "[outcomes] capture: owl_registry.get failed — dna_snapshot omitted",
                    exc_info=exc,
                    extra={"_fields": {"owl_name": state.owl_name}},
                )
        await store.record(
            trace_id=state.trace_id,
            session_id=state.session_id,
            owl_name=state.owl_name,
            channel=state.channel,
            success=trustworthy_success,
            latency_ms=total_ms,
            tool_call_count=len(state.tool_calls),
            failure_class=failure_class,
            step_durations=dict(state.step_durations),
            input_text=state.input_text,
            response_text=response_text,
            tool_sequence=tuple(tc.tool_name for tc in state.tool_calls),
            dna_snapshot=dna_snapshot,
            overclaim_blocked=state.overclaim_blocked,
            recovered_via_tool=recovered_via_tool,
            failed_capability=failed_capability,
            retry_lineage_id=state.retry_lineage_id,
            retry_event_count=retry_event_count,
        )
        # LS7 — close the skill-usage loop: nudge applied skills' success_rate
        # from this turn's MEASURED outcome. Internally fail-open.
        await _update_skill_success_rates(
            services, state, success=trustworthy_success,
        )
        # Single-failure learning — a novel failure otherwise gets ZERO learning
        # signal until IncidentEscalationHandler clusters >= 3 occurrences of the
        # SAME (capability_class, failure_class) within its 10-minute sweep
        # window (see that handler's memory-write for the cluster-verified,
        # higher-confidence counterpart). This is the cheap, immediate half:
        # one LOW-TRUST staged fact per single failure, gated on the SAME
        # health_loop switch, so recall (classify.py's lesson_context) can
        # weight it below an RCA-verified skill lesson.
        if failure_class is not None and services.memory_bridge is not None:
            try:
                from stackowl.config.settings import Settings

                if Settings().health_loop:
                    from stackowl.memory.models import StagedFact

                    await services.memory_bridge.stage(StagedFact(
                        content=(
                            f"Attempted {state.input_text[:200]!r} via "
                            f"{state.owl_name}, failed: {failure_class}"
                        ),
                        source_type="agent_self",
                        source_ref=state.trace_id,
                        confidence=0.3,
                        trust="untrusted",
                    ))
            except Exception as exc:  # B5 — single-failure learning must not block capture
                log.engine.debug(
                    "[outcomes] capture: single-failure memory stage failed",
                    exc_info=exc,
                    extra={"_fields": {
                        "trace_id": state.trace_id, "failure_class": failure_class,
                    }},
                )
    except Exception as exc:  # B5 — log, never raise from telemetry
        log.engine.warning(
            "[outcomes] capture: write failed — telemetry lost for this turn",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return
    # 4. EXIT
    log.engine.info(
        "[outcomes] capture: exit",
        extra={"_fields": {
            "trace_id": state.trace_id,
            "success": trustworthy_success,
            "failure_class": failure_class,
            "latency_ms": int(total_ms),
        }},
    )
