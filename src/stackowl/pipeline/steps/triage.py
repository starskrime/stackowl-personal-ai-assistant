"""Pipeline step 1: triage — route the incoming request to a target owl.

Routing precedence:
1. ``state.owl_name != "secretary"`` → direct-address from the
   :class:`GatewayScanner`. Validate against the registry, otherwise demote
   to the secretary fallback.
2. Otherwise → call :class:`SecretaryRouter` (LLM intent classifier).
3. If registries are missing → pass through to the secretary.
"""

from __future__ import annotations

from stackowl.exceptions import OwlNotFoundError
from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState
from stackowl.setup.lang_detect import detect_language

_FALLBACK_OWL = "secretary"


async def run(state: PipelineState) -> PipelineState:
    """Route incoming request to the target owl."""
    log.engine.info(
        "[pipeline] triage: entry",
        extra={
            "_fields": {
                "trace_id": state.trace_id,
                "session_id": state.session_id,
                "owl": state.owl_name,
            }
        },
    )

    services = get_services()
    owl_registry = services.owl_registry
    provider_registry = services.provider_registry

    if state.owl_name != _FALLBACK_OWL:
        if owl_registry is None:
            log.engine.debug(
                "[pipeline] triage: no owl_registry — accepting direct address as-is",
                extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
            )
            log.engine.info(
                "[pipeline] triage: direct address",
                extra={
                    "_fields": {
                        "trace_id": state.trace_id,
                        "owl": state.owl_name,
                        "latency_ms": 0,
                    }
                },
            )
            return state
        try:
            owl_registry.get(state.owl_name)
        except OwlNotFoundError as exc:
            log.engine.warning(
                "[pipeline] triage: unknown direct-address owl — routing to secretary",
                exc_info=exc,
                extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
            )
            return state.evolve(owl_name=_FALLBACK_OWL)
        log.engine.info(
            "[pipeline] triage: direct address",
            extra={
                "_fields": {
                    "trace_id": state.trace_id,
                    "owl": state.owl_name,
                    "latency_ms": 0,
                }
            },
        )
        return state

    if owl_registry is None or provider_registry is None:
        log.engine.debug(
            "[pipeline] triage: registries missing — pass-through to secretary",
            extra={
                "_fields": {
                    "trace_id": state.trace_id,
                    "has_owl_registry": owl_registry is not None,
                    "has_provider_registry": provider_registry is not None,
                }
            },
        )
        return state.evolve(owl_name=_FALLBACK_OWL)

    # Import here to avoid pulling provider/cost machinery during module load.
    from stackowl.owls.router import SecretaryRouter

    router = SecretaryRouter(
        provider_registry=provider_registry,
        owl_registry=owl_registry,
    )
    log.engine.debug(
        "[pipeline] triage: invoking SecretaryRouter",
        extra={"_fields": {"trace_id": state.trace_id}},
    )

    result = await router.route(state)
    # F089/F098 — stamp the turn's coarse language here (the established evolve
    # seam) so a provider-down honest floor can localize without any model call.
    language = detect_language(state.input_text)
    log.engine.info(
        "[pipeline] triage: routed",
        extra={
            "_fields": {
                "trace_id": state.trace_id,
                "owl": result.owl_name,
                "intent_class": result.intent_class,
                "language": language,
            }
        },
    )
    return state.evolve(
        owl_name=result.owl_name,
        intent_class=result.intent_class,
        clarify_question=result.clarify_question,
        language=language,
        intent_classified=True,
    )
