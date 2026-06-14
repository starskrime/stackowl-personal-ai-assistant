"""Shared provider-selection helper for the execute and assemble pipeline steps.

Extracted from execute.py so that assemble can resolve the tool-loop provider
quietly (log_selection=False) before execute re-selects with the default INFO
logging (log_selection=True).
"""
from __future__ import annotations

from stackowl.commands.tier_command import get_session_tier
from stackowl.exceptions import ProviderNotFoundError
from stackowl.infra import recovery_context
from stackowl.infra.observability import log
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import ModelProvider
from stackowl.providers.registry import ProviderRegistry


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
    try:
        provider = registry.get(state.owl_name)
        if log_selection:
            log.engine.info(
                "[pipeline] execute: tool provider selected",
                extra={"_fields": {
                    "owl": state.owl_name,
                    "chosen_provider_name": state.owl_name,
                    "source": "owl_named_provider",
                }},
            )
        return provider
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
            return provider
        except ProviderNotFoundError:
            log.engine.warning(
                "[pipeline] execute: manifest provider_name not registered — falling back to tier",
                extra={"_fields": {"owl": state.owl_name, "provider_name": manifest.provider_name}},
            )

    # --- Step 3: Determine desired tier (session pref > manifest > default) ---
    session_tier = get_session_tier(state.session_id)
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
    return provider
