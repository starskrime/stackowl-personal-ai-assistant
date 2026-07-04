"""AsyncioBackend — sequential asyncio pipeline executor (ARCH-114 full fallback)."""

from __future__ import annotations

import time

from stackowl.infra import decision_ledger, recovery_context, tool_outcome_ledger
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.pipeline import lesson_context as lc
from stackowl.pipeline.backends.base import OrchestratorBackend
from stackowl.pipeline.backends.shared import _capture_outcome, _verify_turn_acceptance, run_delivery_gate
from stackowl.pipeline.budget import human_wait as human_wait_ctx
from stackowl.pipeline.progress.emitter import emit_start as emit_progress_start
from stackowl.pipeline.progress.emitter import make_progress_callback
from stackowl.pipeline.registry import PIPELINE_STEPS
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState, StepError
from stackowl.pipeline.step_error import format_step_error
from stackowl.pipeline.steps import deliver


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
        # Task 2 — surface "Working on it…" the instant the turn begins, BEFORE
        # triage's LLM-based router call (and the embedding/memory/graph reads in
        # classify.py/assemble.py) rather than after — moved from execute.py's tool
        # loop, which fired only once the turn had already reached the tool-call
        # path, well after those unacked calls. Same is_eligible() gating (settings
        # state is already fully populated by the gateway at this point); a
        # gated/ineligible turn still composes no callback ⇒ no-op, byte-identical.
        await emit_progress_start(make_progress_callback(current, self._services))
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

            # FR-11/FR-12 shared seam — gate cascade (applied_lessons → recovery →
            # persistence_handoff → giveup floor → overclaim → grounding →
            # critical_failure → command_hint) + F088 persist_turn ordering, now
            # owned by pipeline/backends/shared.py so both backends call the same
            # sequence instead of each carrying its own copy.
            current = await run_delivery_gate(current, self._services)

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
