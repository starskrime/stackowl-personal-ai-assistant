"""AsyncioBackend — sequential asyncio pipeline executor (ARCH-114 full fallback)."""

from __future__ import annotations

import time

from stackowl.infra.observability import log
from stackowl.pipeline.backends.base import OrchestratorBackend
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
        log.engine.debug(
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
        current = state
        try:
            for step_name, step_fn in PIPELINE_STEPS:
                current = current.evolve(pipeline_step=step_name)
                step_t0 = time.monotonic()
                try:
                    current = await step_fn(current)
                    duration_ms = (time.monotonic() - step_t0) * 1000
                    log.engine.debug(
                        "[asyncio_backend] run: step ok",
                        extra={"_fields": {"step": step_name, "trace_id": state.trace_id, "duration_ms": duration_ms}},
                    )
                except Exception as exc:
                    duration_ms = (time.monotonic() - step_t0) * 1000
                    error_msg = f"{step_name}: {type(exc).__name__}: {exc}"
                    log.engine.error(
                        "[asyncio_backend] run: step failed — %s",
                        error_msg,
                        exc_info=True,
                        extra={"_fields": {"step": step_name, "trace_id": state.trace_id, "duration_ms": duration_ms}},
                    )
                    current = current.evolve(errors=(*current.errors, error_msg))

            current = current.evolve(pipeline_step="deliver")
            deliver_t0 = time.monotonic()
            try:
                current = await deliver.run(current)
                deliver_ms = (time.monotonic() - deliver_t0) * 1000
                log.engine.debug(
                    "[asyncio_backend] run: step ok",
                    extra={"_fields": {"step": "deliver", "trace_id": state.trace_id, "duration_ms": deliver_ms}},
                )
            except Exception as exc:
                deliver_ms = (time.monotonic() - deliver_t0) * 1000
                error_msg = f"deliver: {type(exc).__name__}: {exc}"
                log.engine.error(
                    "[asyncio_backend] run: deliver failed — %s",
                    error_msg,
                    exc_info=True,
                    extra={"_fields": {"step": "deliver", "trace_id": state.trace_id, "duration_ms": deliver_ms}},
                )
                current = current.evolve(errors=(*current.errors, error_msg))
        finally:
            reset_services(token)

        total_ms = (time.monotonic() - t0) * 1000
        log.engine.debug(
            "[asyncio_backend] run: exit",
            extra={"_fields": {"trace_id": state.trace_id, "total_ms": total_ms, "error_count": len(current.errors)}},
        )
        return current
