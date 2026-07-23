"""AsyncioBackend — sequential asyncio pipeline executor (ARCH-114 full fallback)."""

from __future__ import annotations

import asyncio
import time

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.backends.base import OrchestratorBackend
from stackowl.pipeline.backends.shared import (
    _capture_outcome,
    _verify_turn_acceptance,
    bind_turn_context,
    run_delivery_gate,
    unbind_turn_context,
)
from stackowl.pipeline.progress.emitter import bind_turn_callback as bind_progress_callback
from stackowl.pipeline.progress.emitter import emit_start as emit_progress_start
from stackowl.pipeline.progress.emitter import make_progress_callback
from stackowl.pipeline.progress.emitter import reset_turn_callback as reset_progress_callback
from stackowl.pipeline.registry import PIPELINE_STEPS
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState, StepError
from stackowl.pipeline.step_error import format_step_error
from stackowl.pipeline.steps import deliver
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.pipeline.supervisor import synthesize_floor


class AsyncioBackend(OrchestratorBackend):
    """Executes the 8 pipeline steps sequentially using plain asyncio.

    This is the full fallback backend per ARCH-114 — not a stub.
    parliament_step uses asyncio.gather fan-out when multiple owls are present;
    in Epic 2 this is a single-owl stub; real fan-out is wired in Epic 5.
    """

    def __init__(self, *, services: StepServices | None = None) -> None:
        self._services = services or StepServices()

    async def _run_steps(
        self, current: PipelineState, step_durations: list[tuple[str, float]]
    ) -> PipelineState:
        """The registered step loop, extracted so run() can bound it with a deadline.

        Cancellation-safe: holds no cross-turn resources of its own — governor
        slots and trace spans are `async with`/finally-released inside the steps,
        and every context binding lives in run()'s try/finally, outside this
        coroutine.
        """
        for step_name, step_fn in PIPELINE_STEPS:
            current = current.evolve(pipeline_step=step_name)
            step_t0 = time.monotonic()
            try:
                async with TraceContext.span(f"step.{step_name}"):
                    current = await step_fn(current)
                duration_ms = (time.monotonic() - step_t0) * 1000
                step_durations.append((step_name, duration_ms))
                log.engine.info(
                    "[asyncio_backend] run: step ok",
                    extra={"_fields": {"step": step_name, "trace_id": current.trace_id, "duration_ms": duration_ms}},
                )
            except Exception as exc:
                duration_ms = (time.monotonic() - step_t0) * 1000
                step_durations.append((step_name, duration_ms))
                error_msg = format_step_error(step_name, exc)
                log.engine.error(
                    "[asyncio_backend] run: step failed — %s",
                    error_msg,
                    exc_info=True,
                    extra={"_fields": {"step": step_name, "trace_id": current.trace_id, "duration_ms": duration_ms}},
                )
                # REACT-7/F092 — write the structured record in lockstep with the
                # human string so the critical-failure honesty surface reads typed
                # fields, not a re-parsed (drift-prone) string.
                current = current.evolve(
                    errors=(*current.errors, error_msg),
                    step_errors=(*current.step_errors,
                                 StepError(step=step_name, exc_type=type(exc).__name__, message=str(exc))),
                )

            # C1 fix (final whole-branch review) — Task 7's manual "do it
            # again" hook (triage.run) already dispatched+delivered a retry
            # via RetryActuator.attempt_retry (which itself edits/sends the
            # answer). Running the REMAINING pipeline steps plus the delivery
            # gate + deliver.run on the raw "do it again" text would produce
            # a SECOND response to the user. Short-circuit the instant a step
            # sets it (only triage ever does, so this fires right after it).
            if current.retry_dispatched:
                log.engine.info(
                    "[asyncio_backend] run: retry_dispatched — short-circuiting remaining pipeline",
                    extra={"_fields": {"trace_id": current.trace_id, "step": step_name}},
                )
                break
        return current

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
        bindings = bind_turn_context(state, self._services)
        current = state
        step_durations: list[tuple[str, float]] = []
        # Task 2 — surface "Working on it…" the instant the turn begins, BEFORE
        # triage's LLM-based router call (and the embedding/memory/graph reads in
        # classify.py/assemble.py) rather than after — moved from execute.py's tool
        # loop, which fired only once the turn had already reached the tool-call
        # path, well after those unacked calls. Same is_eligible() gating (settings
        # state is already fully populated by the gateway at this point); a
        # gated/ineligible turn still composes no callback ⇒ no-op, byte-identical.
        # bind_progress_callback stashes THIS SAME callback/emitter (turn-scoped
        # ContextVar, reset in `finally` below) so execute.py's tool loop reuses it
        # for per-iteration updates instead of building a second emitter — keeping
        # ONE continuous step_index counter (PipelineStrip renders it as a glyph
        # "train"; two independent emitters made it stall for one step at the
        # ack→first-iteration boundary).
        _progress_cb = make_progress_callback(current, self._services)
        progress_token = bind_progress_callback(_progress_cb)
        await emit_progress_start(_progress_cb)
        try:
            # Global interactive turn deadline — computed once by
            # bind_turn_context (2026-07 incident: a telegram turn hung
            # 1670+s — lower-level timeouts like resilient_round don't cover
            # every hang, e.g. a wedged tool call). Interactive turns only.
            deadline_s = bindings.deadline_s
            if deadline_s > 0:
                try:
                    current = await asyncio.wait_for(
                        self._run_steps(current, step_durations), timeout=deadline_s
                    )
                except TimeoutError:
                    # Intermediate step state died with the cancelled task; the
                    # pre-steps state + this error is everything that survives.
                    error_msg = (
                        f"deadline: TimeoutError: interactive turn exceeded "
                        f"{deadline_s:.0f}s deadline"
                    )
                    log.engine.error(
                        "[asyncio_backend] run: interactive turn deadline exceeded — cancelled",
                        extra={"_fields": {"trace_id": state.trace_id, "deadline_s": deadline_s}},
                    )
                    # Responses-only invariant: the floor ADDS an honest chunk;
                    # the error STAYS in errors so task_outcomes records a real
                    # failure (classify_failure → "TimeoutError").
                    floor_chunk = ResponseChunk(
                        content=synthesize_floor(
                            goal=state.input_text,
                            error=f"turn cancelled after {deadline_s:.0f}s deadline",
                            attempts=[],
                            partial="",
                        ),
                        is_final=False,
                        chunk_index=0,
                        trace_id=state.trace_id,
                        owl_name=state.owl_name,
                        is_floor=True,
                    )
                    current = current.evolve(
                        responses=(*current.responses, floor_chunk),
                        errors=(*current.errors, error_msg),
                        step_errors=(*current.step_errors,
                                     StepError(step="deadline", exc_type="TimeoutError",
                                               message=f"interactive turn exceeded {deadline_s:.0f}s deadline")),
                    )
            else:
                current = await self._run_steps(current, step_durations)

            if current.retry_dispatched:
                log.engine.debug(
                    "[asyncio_backend] run: retry_dispatched — skipping delivery gate + deliver",
                    extra={"_fields": {"trace_id": state.trace_id}},
                )
            else:
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
            reset_progress_callback(progress_token)
            retry_event_count = await unbind_turn_context(
                bindings, state, self._services, backend_name="asyncio_backend",
            )

        # Persist the measured step durations onto the final state for the
        # outcome-capture helper to read.
        current = current.evolve(step_durations=tuple(step_durations))

        total_ms = (time.monotonic() - bindings.t0) * 1000
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
        acceptance = await _verify_turn_acceptance(current, bindings.wall_t0, self._services)

        # Outcome capture — best-effort; never block the response on a
        # telemetry write failure. Helper logs its own warning on error.
        await _capture_outcome(
            current, total_ms, self._services,
            acceptance=acceptance, retry_event_count=retry_event_count,
        )
        return current
