"""Pipeline step 2: dispatch — validate target owl and route delegation.

The Secretary-to-specialist A2A round-trip is orchestrated separately in
``stackowl.owls.a2a_delegation.A2ADelegator``; this step's role is to verify
that the requested owl is known and to fall back to Secretary otherwise.
"""

from __future__ import annotations

from stackowl.exceptions import OwlNotFoundError
from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState

_SECRETARY_NAME = "secretary"


async def run(state: PipelineState) -> PipelineState:
    """Validate ``state.owl_name`` against the OwlRegistry.

    Behavior:
      * Pass-through when no A2AQueue / OwlRegistry is wired (early-stage tests).
      * Pass-through when the target is already Secretary.
      * Falls back to Secretary when the requested owl is unknown.
    """
    log.engine.debug(
        "[pipeline] dispatch: entry",
        extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
    )

    services = get_services()
    a2a_queue = services.a2a_queue
    owl_registry = services.owl_registry

    if a2a_queue is None or owl_registry is None:
        log.engine.debug(
            "[pipeline] dispatch: pass-through (no delegation services)",
            extra={
                "_fields": {
                    "trace_id": state.trace_id,
                    "has_queue": a2a_queue is not None,
                    "has_registry": owl_registry is not None,
                }
            },
        )
        return state

    if state.owl_name == _SECRETARY_NAME:
        log.engine.debug(
            "[pipeline] dispatch: target is secretary — pass-through",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state

    try:
        owl_registry.get(state.owl_name)
    except OwlNotFoundError as exc:
        log.engine.warning(
            "[pipeline] dispatch: unknown owl, routing to secretary",
            exc_info=exc,
            extra={
                "_fields": {
                    "trace_id": state.trace_id,
                    "requested_owl": state.owl_name,
                    "fallback": _SECRETARY_NAME,
                }
            },
        )
        return state.evolve(owl_name=_SECRETARY_NAME)

    log.engine.info(
        "[pipeline] dispatch: exit",
        extra={
            "_fields": {
                "trace_id": state.trace_id,
                "owl": state.owl_name,
                "delegation_ready": True,
            }
        },
    )
    return state
