"""AsyncioBackend — sequential asyncio pipeline executor (ARCH-114 full fallback)."""

from __future__ import annotations

import time

from stackowl.infra import recovery_context, tool_outcome_ledger
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.memory.outcome_store import TaskOutcomeStore, classify_failure
from stackowl.pipeline import lesson_context as lc
from stackowl.pipeline.applied_lessons import surface_applied_lessons
from stackowl.pipeline.backends.base import OrchestratorBackend
from stackowl.pipeline.critical_failure import surface_critical_failure
from stackowl.pipeline.giveup_floor import surface_consequential_giveup_floor
from stackowl.pipeline.recovery_summary import surface_recovery
from stackowl.pipeline.registry import PIPELINE_STEPS
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
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
        token = set_services(self._services)
        trace_token = TraceContext.start(
            state.session_id,
            trace_id=state.trace_id,
            interactive=state.interactive,
            channel=state.channel,
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
                    error_msg = f"{step_name}: {type(exc).__name__}: {exc}"
                    log.engine.error(
                        "[asyncio_backend] run: step failed — %s",
                        error_msg,
                        exc_info=True,
                        extra={"_fields": {"step": step_name, "trace_id": state.trace_id, "duration_ms": duration_ms}},
                    )
                    current = current.evolve(errors=(*current.errors, error_msg))

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
            # Phase 2 #2 — surface a CRITICAL (execute) step failure to the user
            # BEFORE deliver, so silence is replaced by a localized apology. Shared
            # with LangGraphBackend; self-healing (never raises into the backend).
            current = await surface_critical_failure(current, self._services)

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
                error_msg = f"deliver: {type(exc).__name__}: {exc}"
                log.engine.error(
                    "[asyncio_backend] run: deliver failed — %s",
                    error_msg,
                    exc_info=True,
                    extra={"_fields": {"step": "deliver", "trace_id": state.trace_id, "duration_ms": deliver_ms}},
                )
                current = current.evolve(errors=(*current.errors, error_msg))
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
        # Outcome capture — best-effort; never block the response on a
        # telemetry write failure. Helper logs its own warning on error.
        await _capture_outcome(current, total_ms, self._services)
        return current


async def _capture_outcome(
    state: PipelineState, total_ms: float, services: StepServices,
) -> None:
    """Persist a row in task_outcomes for this run. Best-effort — logs on failure.

    Captures: success (no errors), latency_ms, tool_call_count, failure_class
    (from state.errors via classify_failure), step_durations, input_text,
    response_text. quality_score / scored_at start NULL — the CriticScorerHandler
    fills them in asynchronously later.
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
            success=len(state.errors) == 0,
            latency_ms=total_ms,
            tool_call_count=len(state.tool_calls),
            failure_class=failure_class,
            step_durations=dict(state.step_durations),
            input_text=state.input_text,
            response_text=response_text,
            tool_sequence=tuple(tc.tool_name for tc in state.tool_calls),
            dna_snapshot=dna_snapshot,
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
            "success": len(state.errors) == 0,
            "failure_class": failure_class,
            "latency_ms": int(total_ms),
        }},
    )
