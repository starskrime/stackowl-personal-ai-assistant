"""Pipeline step 1: triage — route the incoming request to a target owl.

Routing precedence:
1. ``state.owl_name != "secretary"`` → direct-address from the
   :class:`GatewayScanner`. Validate against the registry, otherwise demote
   to the secretary fallback.
2. Otherwise, if registries are missing → pass through to the secretary.
3. Otherwise, FR-9 sticky-routing bypass: a short (<200 char) message in a
   session with a fresh (<=30 min) cached owl resolution reuses that owl +
   intent_class, skipping the router entirely. See
   :mod:`stackowl.owls.sticky_route_cache`.
4. Otherwise → call :class:`SecretaryRouter` (LLM intent classifier); a
   non-clarify result seeds the sticky cache for the next turn.
"""

from __future__ import annotations

from stackowl.exceptions import OwlNotFoundError
from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState
from stackowl.setup.lang_detect import detect_language

_FALLBACK_OWL = "secretary"

# FR-9 — sticky-routing bypass length ceiling. Mirrors FR-8's
# ``feedback.py:_PREFILTER_MAX_CHARS`` value; redefined locally rather than
# imported to avoid coupling two unrelated pipeline steps.
_STICKY_MAX_CHARS = 200


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

    # FR-9 — sticky-routing bypass: reuse the previous turn's owl + intent_class
    # for a short, same-session follow-up instead of calling the LLM router.
    # Purely mechanical (no new-topic detection); ALL conditions must hold.
    sticky_cache = services.sticky_route_cache
    if sticky_cache is not None and len(state.input_text) < _STICKY_MAX_CHARS:
        cached = sticky_cache.get(state.session_id)
        # Adversarial review (2026-07-01) — restrict reuse to "conversational"
        # cached entries only. A "standard" (work-turn) resolution is the one
        # most likely to be stale by the time a short follow-up arrives, and
        # reusing it silently defeats the F120 tool-capability gate
        # (provider_select.py short-circuits tool-capability checks for
        # conversational/clarify classes) and the answer-floor tier — a real
        # new task disguised as a short message would get a non-tool-capable
        # provider and a "fast" floor instead of "standard". Never worth the
        # LLM-call savings; only the low-risk conversational-follow-up case
        # ("ok thanks", "sounds good") is fast-pathed.
        if cached is not None and cached[1] != "conversational":
            cached = None
        if cached is not None:
            cached_owl, cached_intent_class = cached
            try:
                owl_registry.get(cached_owl)
            except OwlNotFoundError as exc:
                log.engine.warning(
                    "[pipeline] triage: sticky-cached owl no longer valid — falling through to router",
                    exc_info=exc,
                    extra={"_fields": {"trace_id": state.trace_id, "owl": cached_owl}},
                )
            else:
                language = detect_language(state.input_text)
                log.engine.info(
                    "[pipeline] triage: sticky-routed",
                    extra={
                        "_fields": {
                            "trace_id": state.trace_id,
                            "owl": cached_owl,
                            "intent_class": cached_intent_class,
                            "language": language,
                        }
                    },
                )
                return state.evolve(
                    owl_name=cached_owl,
                    intent_class=cached_intent_class,
                    clarify_question=None,
                    language=language,
                    intent_classified=True,
                )

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
    # FR-9 — seed the sticky cache for the NEXT turn, but ONLY a "conversational"
    # resolution (the read side above never reuses anything else — see the
    # adversarial-review comment there; "standard"/"clarify" results are
    # deliberately never cached, not just never read).
    if sticky_cache is not None and result.intent_class == "conversational":
        sticky_cache.set(state.session_id, result.owl_name, result.intent_class)
    return state.evolve(
        owl_name=result.owl_name,
        intent_class=result.intent_class,
        clarify_question=result.clarify_question,
        language=language,
        intent_classified=True,
    )
