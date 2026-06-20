"""Shared provider-selection helper for the execute and assemble pipeline steps.

Extracted from execute.py so that assemble can resolve the tool-loop provider
quietly (log_selection=False) before execute re-selects with the default INFO
logging (log_selection=True).
"""
from __future__ import annotations

from stackowl.commands.tier_command import get_session_tier
from stackowl.exceptions import (
    AllProvidersUnavailableError,
    ProviderNotFoundError,
    ToolUseUnsupportedError,
)
from stackowl.infra import recovery_context
from stackowl.infra.observability import log
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.pipeline.state import TOOL_FREE_CLASSES, PipelineState
from stackowl.providers.base import ModelProvider
from stackowl.providers.registry import ProviderRegistry

# Tier walk order for the F120 route-away cascade (mirrors registry._TIER_ORDER).
_TOOL_CAPABLE_TIER_WALK: tuple[str, ...] = ("powerful", "standard", "fast", "local")


def _ensure_tool_capable(
    provider: ModelProvider,
    registry: ProviderRegistry,
    state: PipelineState,
    *,
    log_selection: bool,
) -> ModelProvider:
    """F120 capability gate: for an AGENTIC turn, never return a provider that can't
    act (``supports_tools is False``).

    A conversational turn is untouched (Gemini's stream/complete path is fine — gate
    the tool loop, not the provider). For an agentic turn, if the chosen provider
    cannot run the tool loop, cascade across tiers to the first tool-capable provider
    (logging LOUDLY). If NONE exists, raise :class:`ToolUseUnsupportedError` so the
    execute step floors HONESTLY ("I can't act with this model") — never a silent
    tool-free reply.
    """
    if state.intent_class in TOOL_FREE_CLASSES:
        return provider
    # Duck-typed test fakes (not ModelProvider subclasses) may lack supports_tools —
    # default True (tool-capable) so they pass through byte-identically, mirroring
    # the getattr-guarded cost-tracker/resilience injection. Only a provider that
    # EXPLICITLY declares supports_tools=False is gated.
    if getattr(provider, "supports_tools", True):
        return provider

    log.engine.warning(
        "[pipeline] execute: selected provider cannot call tools on an agentic turn — "
        "routing to a tool-capable tier",
        extra={"_fields": {
            "owl": state.owl_name,
            "incapable_provider": getattr(provider, "name", type(provider).__name__),
            "intent_class": state.intent_class,
        }},
    )
    seen: set[int] = set()
    for tier in _TOOL_CAPABLE_TIER_WALK:
        try:
            candidate, _degraded = registry.resolve_tier_with_fallback(tier)
        except AllProvidersUnavailableError:
            continue
        if id(candidate) in seen:
            continue
        seen.add(id(candidate))
        if getattr(candidate, "supports_tools", True):
            if log_selection:
                log.engine.info(
                    "[pipeline] execute: routed agentic turn to a tool-capable provider",
                    extra={"_fields": {
                        "owl": state.owl_name,
                        "chosen_provider_name": getattr(candidate, "name", "?"),
                        "source": "tool_capability_route_away",
                    }},
                )
            return candidate

    # No tool-capable provider anywhere → floor honestly (caller catches and surfaces).
    log.engine.error(
        "[pipeline] execute: no tool-capable provider for an agentic turn — flooring honestly",
        extra={"_fields": {"owl": state.owl_name, "intent_class": state.intent_class}},
    )
    raise ToolUseUnsupportedError(getattr(provider, "name", type(provider).__name__))


def _warn_owl_name_shadow(services: object, state: PipelineState) -> None:
    """Warn when an owl-named provider is about to shadow an explicit, DIFFERENT
    ``manifest.provider_name`` pin (F031/REACT-3). Best-effort: a manifest lookup
    failure is swallowed (logged at debug) — the warn is an observability aid, never
    a gate, so it must never block provider selection."""
    owl_reg = getattr(services, "owl_registry", None)
    if owl_reg is None:
        return
    try:
        manifest = owl_reg.get(state.owl_name)
    except Exception as exc:  # unknown owl / registry fault — no manifest to compare
        log.engine.debug(
            "[pipeline] execute: owl-name-shadow check: manifest lookup failed",
            exc_info=exc, extra={"_fields": {"owl": state.owl_name}},
        )
        return
    pin = getattr(manifest, "provider_name", None)
    if pin and pin != state.owl_name:
        log.engine.warning(
            "[pipeline] execute: owl-named provider SHADOWS an explicit manifest "
            "provider_name pin — the owl-name binding wins (most-specific); the "
            "manifest pin is ignored this turn",
            extra={"_fields": {
                "owl": state.owl_name,
                "owl_named_provider": state.owl_name,
                "shadowed_manifest_pin": pin,
            }},
        )


def select_tool_provider(
    registry: ProviderRegistry,
    services: object,
    state: PipelineState,
    *,
    log_selection: bool = True,
    record_recovery: bool = True,
) -> ModelProvider:
    """Resolve the ModelProvider for the tool-use loop.

    Precedence (highest → lowest):
    0. A provider registered under the OWL's own name — the most specific per-owl
       binding. If this wins while the manifest ALSO carries a DIFFERENT explicit
       ``provider_name`` pin, the collision is logged at WARN (F031/REACT-3): the
       owl-name binding still wins, but the shadowed manifest pin is now visible.
    1. Owl manifest ``provider_name`` pin — if set and registered, use it directly.
       On ProviderNotFoundError warn and fall through to tier routing.
    2. Desired tier = get_session_tier(session_id) OR manifest.model_tier OR "powerful".
       Session pref beats manifest; manifest beats default.
    3. Resolve via registry.resolve_tier_with_fallback(desired_tier) — circuit-aware
       (falls back if the tier provider's circuit is OPEN).

    ``log_selection`` gates the INFO "[pipeline] execute: tool provider selected"
    records emitted at each branch.  Set to False when resolving quietly (e.g. in
    assemble); leave as True (the default) for the execute step so behaviour is
    byte-identical to the original.
    """
    log.engine.debug(
        "[pipeline] execute: _select_tool_provider: entry",
        extra={"_fields": {"owl": state.owl_name, "session": state.session_id}},
    )

    # --- Step 0: A provider registered under the owl's own name wins (a
    # per-owl provider binding). This is the most specific pin. ---
    #
    # F031/REACT-3 precedence (documented): owl-named provider > manifest.provider_name
    # > session/manifest tier. The owl-named provider intentionally wins because it is
    # the most specific per-owl binding. BUT when the owl's manifest ALSO carries an
    # explicit, DIFFERENT provider_name pin, the user made a deliberate routing choice
    # that this step silently overrides — so warn (collision is now VISIBLE, not buried
    # in a debug line). A matching pin (or no pin) is not a collision and stays quiet.
    try:
        provider = registry.get(state.owl_name)
        if record_recovery:  # only the loud (execute) selection emits the collision warn
            _warn_owl_name_shadow(services, state)
        if log_selection:
            log.engine.info(
                "[pipeline] execute: tool provider selected",
                extra={"_fields": {
                    "owl": state.owl_name,
                    "chosen_provider_name": state.owl_name,
                    "source": "owl_named_provider",
                }},
            )
        return _ensure_tool_capable(provider, registry, state, log_selection=log_selection)
    except ProviderNotFoundError:
        pass  # no per-owl provider — fall through to manifest/tier routing

    # --- Step 1: Fetch manifest (best-effort) ---
    manifest: OwlAgentManifest | None = None
    owl_reg = getattr(services, "owl_registry", None)
    if owl_reg is not None:
        try:
            manifest = owl_reg.get(state.owl_name)
        except Exception as exc:
            # Expected for an unknown owl; logged (never silent) so a registry
            # fault is distinguishable from a benign not-found.
            log.engine.debug(
                "[pipeline] execute: owl manifest lookup failed — tier routing only",
                exc_info=exc,
                extra={"_fields": {"owl": state.owl_name}},
            )
            manifest = None

    # --- Step 2: Explicit provider pin ---
    if manifest is not None and manifest.provider_name:
        try:
            provider = registry.get(manifest.provider_name)
            if log_selection:
                log.engine.info(
                    "[pipeline] execute: tool provider selected",
                    extra={"_fields": {
                        "owl": state.owl_name,
                        "desired_tier": manifest.model_tier,
                        "chosen_provider_name": manifest.provider_name,
                        "source": "manifest_pin",
                    }},
                )
            return _ensure_tool_capable(provider, registry, state, log_selection=log_selection)
        except ProviderNotFoundError:
            log.engine.warning(
                "[pipeline] execute: manifest provider_name not registered — falling back to tier",
                extra={"_fields": {"owl": state.owl_name, "provider_name": manifest.provider_name}},
            )

    # --- Step 3: Determine desired tier (session pref > manifest > default) ---
    # Use identity_key when set so cross-channel /tier takes effect: the tier
    # is written under identity_key or session_id (see tier_command._owner_key_for_state).
    session_tier = get_session_tier(state.identity_key or state.session_id)
    if session_tier:
        desired = session_tier
        tier_source = "session"
    elif manifest is not None and manifest.model_tier:
        desired = manifest.model_tier
        tier_source = "manifest"
    else:
        desired = "powerful"
        tier_source = "default"
        if manifest is None:
            log.engine.warning(
                "[pipeline] execute: unknown owl or no manifest — defaulting to 'powerful' tier",
                extra={"_fields": {"owl": state.owl_name}},
            )

    # --- Step 4: Resolve by tier — circuit-aware (falls back if the tier provider's
    # circuit is OPEN; the pins above are honored as-is). ---
    provider, degraded_from = registry.resolve_tier_with_fallback(desired)
    # record_recovery gates the user-visible fallback event: a side-effect-free
    # window-probe selection (assemble) must NOT record it, else the same
    # provider_fallback is surfaced twice (assemble + execute) on one turn.
    if degraded_from is not None and record_recovery:
        recovery_context.record_recovery(
            kind="provider_fallback", failed=degraded_from,
            recovered_via=provider.name, user_visible=True,
        )
    if log_selection:
        log.engine.info(
            "[pipeline] execute: tool provider selected",
            extra={"_fields": {
                "owl": state.owl_name,
                "desired_tier": desired,
                "chosen_provider_name": getattr(provider, "name", type(provider).__name__),
                "source": tier_source,
            }},
        )
    return _ensure_tool_capable(provider, registry, state, log_selection=log_selection)
