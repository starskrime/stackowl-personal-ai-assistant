"""AsyncioBackend — sequential asyncio pipeline executor (ARCH-114 full fallback)."""

from __future__ import annotations

import time

from stackowl.infra import decision_ledger, recovery_context, tool_outcome_ledger
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.memory.outcome_store import TaskOutcomeStore, classify_failure
from stackowl.objectives.model import ExpectedOutcome
from stackowl.pipeline import lesson_context as lc
from stackowl.pipeline.acceptance import AcceptanceChecker, AcceptanceVerdict
from stackowl.pipeline.applied_lessons import surface_applied_lessons
from stackowl.pipeline.backends.base import OrchestratorBackend
from stackowl.pipeline.budget import human_wait as human_wait_ctx
from stackowl.pipeline.command_hint import surface_command_hint
from stackowl.pipeline.critical_failure import surface_critical_failure
from stackowl.pipeline.giveup_floor import surface_consequential_giveup_floor
from stackowl.pipeline.grounding_gate import surface_grounding_gate
from stackowl.pipeline.overclaim_gate import surface_overclaim_gate
from stackowl.pipeline.recovery_summary import surface_recovery
from stackowl.pipeline.registry import PIPELINE_STEPS
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState, StepError
from stackowl.pipeline.step_error import format_step_error
from stackowl.pipeline.steps import deliver
from stackowl.pipeline.turn_persist import persist_turn

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


class AsyncioBackend(OrchestratorBackend):
    """Executes the 8 pipeline steps sequentially using plain asyncio.

    This is the full fallback backend per ARCH-114 — not a stub.
    parliament_step uses asyncio.gather fan-out when multiple owls are present;
    in Epic 2 this is a single-owl stub; real fan-out is wired in Epic 5.
    """

    def __init__(self, *, services: StepServices | None = None) -> None:
        self._services = services or StepServices()

    async def run(self, state: PipelineState) -> PipelineState:
        log.engine.info(
            "[asyncio_backend] run: entry",
            extra={
                "_fields": {
                    "trace_id": state.trace_id,
                    "session_id": state.session_id,
                    "total_steps": len(PIPELINE_STEPS) + 1,
                }
            },
        )
        t0 = time.monotonic()
        # Wall-clock turn start — the acceptance freshness clock (a fresh artifact's
        # mtime is compared against this). monotonic() is unsuitable (not epoch).
        wall_t0 = time.time()
        token = set_services(self._services)
        trace_token = TraceContext.start(
            state.session_id,
            trace_id=state.trace_id,
            interactive=state.interactive,
            channel=state.channel,
            reply_target=state.reply_target,
            delegation_depth=state.delegation_depth,
            delegation_chain=state.delegation_chain,
            owl_name=state.owl_name,
            creation_ceiling=state.creation_ceiling,
            task_id=state.task_id,
            durable_owner_id=state.durable_owner_id,
        )
        lesson_token = lc.bind()
        recovery_token = recovery_context.bind()
        ledger_token = tool_outcome_ledger.bind()
        # ADR-7 — bind the per-turn DecisionLedger only when enabled (default ON; off
        # only if settings explicitly sets decision_ledger=False). Unbound ⇒
        # record_decision no-ops ⇒ byte-identical to the pre-ADR-7 path.
        _settings = self._services.settings
        decision_token = (
            decision_ledger.bind()
            if _settings is None or _settings.decision_ledger
            else None
        )
        human_wait_token = human_wait_ctx.bind()
        current = state
        step_durations: list[tuple[str, float]] = []
        try:
            for step_name, step_fn in PIPELINE_STEPS:
                current = current.evolve(pipeline_step=step_name)
                step_t0 = time.monotonic()
                try:
                    current = await step_fn(current)
                    duration_ms = (time.monotonic() - step_t0) * 1000
                    step_durations.append((step_name, duration_ms))
                    log.engine.info(
                        "[asyncio_backend] run: step ok",
                        extra={"_fields": {"step": step_name, "trace_id": state.trace_id, "duration_ms": duration_ms}},
                    )
                except Exception as exc:
                    duration_ms = (time.monotonic() - step_t0) * 1000
                    step_durations.append((step_name, duration_ms))
                    error_msg = format_step_error(step_name, exc)
                    log.engine.error(
                        "[asyncio_backend] run: step failed — %s",
                        error_msg,
                        exc_info=True,
                        extra={"_fields": {"step": step_name, "trace_id": state.trace_id, "duration_ms": duration_ms}},
                    )
                    # REACT-7/F092 — write the structured record in lockstep with the
                    # human string so the critical-failure honesty surface reads typed
                    # fields, not a re-parsed (drift-prone) string.
                    current = current.evolve(
                        errors=(*current.errors, error_msg),
                        step_errors=(*current.step_errors,
                                     StepError(step=step_name, exc_type=type(exc).__name__, message=str(exc))),
                    )

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
            current = await surface_critical_failure(current, self._services)
            # WS-D issue 3 — additively append a marked NL→command hint (and any
            # routing-correction notice) to a REAL answer. Runs AFTER the honesty
            # floors so it never decorates a floored/failed turn; gated by
            # ui.command_hints (no-op + byte-identical when off). Never raises.
            current = await surface_command_hint(current, self._services)

            # F088 — persist the turn AFTER the honest floor band, synchronously
            # inside the ledger ContextVar binding (persist_turn reads it). On a
            # floored turn this records the user utterance only, never the
            # dressed-up draft — so the dream worker never promotes a lie. Relocated
            # out of consolidate (which used to persist the PRE-floor draft).
            await persist_turn(current)

            current = current.evolve(pipeline_step="deliver")
            deliver_t0 = time.monotonic()
            try:
                current = await deliver.run(current)
                deliver_ms = (time.monotonic() - deliver_t0) * 1000
                step_durations.append(("deliver", deliver_ms))
                log.engine.info(
                    "[asyncio_backend] run: step ok",
                    extra={"_fields": {"step": "deliver", "trace_id": state.trace_id, "duration_ms": deliver_ms}},
                )
            except Exception as exc:
                deliver_ms = (time.monotonic() - deliver_t0) * 1000
                step_durations.append(("deliver", deliver_ms))
                error_msg = format_step_error("deliver", exc)
                log.engine.error(
                    "[asyncio_backend] run: deliver failed — %s",
                    error_msg,
                    exc_info=True,
                    extra={"_fields": {"step": "deliver", "trace_id": state.trace_id, "duration_ms": deliver_ms}},
                )
                current = current.evolve(
                    errors=(*current.errors, error_msg),
                    step_errors=(*current.step_errors,
                                 StepError(step="deliver", exc_type=type(exc).__name__, message=str(exc))),
                )
        finally:
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
            human_wait_ctx.reset(human_wait_token)
            if decision_token is not None:
                # ADR-7 — persist this turn's decisions durably (cross-process /
                # restart-safe) BEFORE reset clears the ledger. Best-effort: a
                # persistence failure must NEVER break the turn (B5).
                _decisions = decision_ledger.get_decisions()
                if self._services.db_pool is not None and state.session_id and _decisions:
                    try:
                        from stackowl.pipeline.decision_store import TurnDecisionStore
                        await TurnDecisionStore(self._services.db_pool).save(
                            session_id=state.session_id,
                            trace_id=state.trace_id,
                            decisions=_decisions,
                        )
                    except Exception as exc:
                        log.engine.error(
                            "[asyncio_backend] run: decision persist failed (swallowed)",
                            exc_info=exc,
                            extra={"_fields": {"session_id": state.session_id}},
                        )
                decision_ledger.reset(decision_token)
            tool_outcome_ledger.reset(ledger_token)
            recovery_context.reset(recovery_token)
            lc.reset(lesson_token)
            TraceContext.reset(trace_token)
            reset_services(token)

        # Persist the measured step durations onto the final state for the
        # outcome-capture helper to read.
        current = current.evolve(step_durations=tuple(step_durations))

        total_ms = (time.monotonic() - t0) * 1000
        log.engine.info(
            "[asyncio_backend] run: exit",
            extra={"_fields": {"trace_id": state.trace_id, "total_ms": total_ms, "error_count": len(current.errors)}},
        )
        # F-11 — goal-level acceptance on the NORMAL turn path. When the turn
        # DECLARED an expected outcome (or the flag-ON LLM layer derives one), a
        # clean run is not proof of effect — observe the declared post-condition
        # against reality. No declared outcome AND the LLM layer OFF (the default) ⇒
        # this returns None ⇒ byte-identical. A refuted verdict makes the captured
        # outcome untrustworthy below (so the positive-only learner skips a false win).
        acceptance = await _verify_turn_acceptance(current, wall_t0, self._services)

        # Outcome capture — best-effort; never block the response on a
        # telemetry write failure. Helper logs its own warning on error.
        await _capture_outcome(current, total_ms, self._services, acceptance=acceptance)
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

    response_text = "\n".join(c.content for c in state.responses if c.content)
    if not response_text.strip():
        return None
    deriver = LlmAcceptanceDeriver(services.provider_registry, tier)
    return await deriver.derive(intent=state.input_text, draft=response_text)


async def _capture_outcome(
    state: PipelineState, total_ms: float, services: StepServices,
    *, acceptance: AcceptanceVerdict | None = None,
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
        response_text = "\n".join(c.content for c in state.responses if c.content)
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
        )
        # LS7 — close the skill-usage loop: nudge applied skills' success_rate
        # from this turn's MEASURED outcome. Internally fail-open.
        await _update_skill_success_rates(
            services, state, success=trustworthy_success,
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
