"""ProviderRegistry — constructs and holds all ModelProvider instances."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, NamedTuple, cast

from stackowl.exceptions import AllProvidersUnavailableError, ProviderNotFoundError
from stackowl.health.status import HealthStatus
from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.net.host_locality import is_local_url
from stackowl.infra.observability import log
from stackowl.providers.base import ModelProvider
from stackowl.providers.circuit_breaker import CircuitBreaker, CircuitState
from stackowl.providers.cost_tracker_helpers import inject_cost_tracker
from stackowl.providers.rate_limiter import RateLimiter
from stackowl.providers.registry_accessors import RegistryAccessorsMixin
from stackowl.providers.tier_selector import TierSelector

if TYPE_CHECKING:
    from stackowl.config.provider import ProviderConfig
    from stackowl.config.settings import Settings
    from stackowl.providers.cost_tracker import CostTracker


_TIER_ORDER: tuple[str, ...] = ("fast", "standard", "powerful", "local")

#: Tiers ordered MOST → LEAST capable for a capability-degrade substitution
#: (F125). When a requested capable tier (e.g. ``powerful``) has no provider, a
#: substitution prefers the next most-capable AVAILABLE tier rather than an
#: arbitrary config-order provider (which could be a weak local model). ``local``
#: is last because, while orthogonal to tier for routing, an unlabeled local
#: backend is the weakest synthesis substitute and must never win over a cloud
#: standard/fast provider.
_CAPABILITY_ORDER: tuple[str, ...] = ("powerful", "standard", "fast", "local")


class ModelRoute(NamedTuple):
    """One routable (model, tiers) pair under a single provider connection.

    ``model`` is the literal model string to pass as ``ModelProvider.stream(
    ..., model=...)``/``.complete(..., model=...)`` — empty string means "use
    the provider's own default_model" (today's byte-identical behavior).
    """

    model: str
    tiers: tuple[str, ...]


def _flatten_routes(tiers: dict[str, tuple[ModelRoute, ...]]) -> dict[str, tuple[str, ...]]:
    """Flatten each provider's ``ModelRoute`` tuple to a plain tier-name tuple.

    Tier-SELECTION logic (``get_by_tier`` / ``get_with_cascade`` / ``TierSelector``
    / ``resolve_tier_with_fallback`` / ``resolve_capable_or_degrade``) only needs
    to know WHICH TIERS a provider serves across ALL its routes, not which model
    serves which tier — per-model dispatch wiring is a later task in this plan.
    This keeps every one of those call sites byte-identical to the
    pre-``ModelRoute`` behavior on top of the new storage shape.
    """
    return {name: tuple(t for route in routes for t in route.tiers) for name, routes in tiers.items()}


def _inject_resilience(
    provider: object,
    breaker: CircuitBreaker | None,
    limiter: RateLimiter | None,
) -> None:
    """Inject the registry-owned breaker+limiter into one provider, if it accepts them.

    Mirrors ``inject_cost_tracker``: duck-typed test fakes (not ``ModelProvider``
    subclasses) lack ``set_resilience`` and simply opt out, so they stay
    byte-identical pass-throughs without breaking. The provider WRITES breaker
    state at its per-round boundary via this exact object — the SAME breaker the
    cascade READS next turn (F115).
    """
    setter = getattr(provider, "set_resilience", None)
    if callable(setter):
        setter(breaker, limiter)


def _inject_cooldown_hours(provider: object, cooldown_hours: float | None) -> None:
    """Inject the registry-owned cooldown_hours into one provider, if it accepts it.

    Mirrors ``_inject_resilience`` — duck-typed test fakes without
    ``set_cooldown_hours`` opt out silently.
    """
    setter = getattr(provider, "set_cooldown_hours", None)
    if callable(setter):
        setter(cooldown_hours)


def _build_provider(config: ProviderConfig, api_key: str) -> ModelProvider:
    """Construct the correct concrete provider for config.protocol."""
    if config.protocol == "anthropic":
        from stackowl.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(config, api_key)
    if config.protocol == "gemini":
        from stackowl.providers.gemini_provider import GeminiProvider

        return GeminiProvider(config, api_key)
    from stackowl.providers.openai_provider import OpenAIProvider

    return OpenAIProvider(config, api_key)


class ProviderRegistry(RegistryAccessorsMixin):
    """Holds ModelProvider references plus per-provider CircuitBreaker and RateLimiter.

    Implements HealthContributor structurally: calls health_check() on all providers.
    Cascade routing (get_with_cascade) selects the first non-OPEN provider across
    tiers fast → standard → powerful → local starting from the preferred tier.
    Name/tier/locality/circuit accessors live in RegistryAccessorsMixin (B2 split).
    """

    def __init__(self, *, clock: Clock = WallClock()) -> None:
        self._clock: Clock = clock
        self._providers: dict[str, ModelProvider] = {}
        self._tiers: dict[str, tuple[ModelRoute, ...]] = {}
        # Locality (self-hosted vs cloud) is ORTHOGONAL to tier (a local Ollama is
        # tier ``fast``); computed from the base_url host so the vision selector can
        # prefer on-box backends without a config migration. Default False (cloud).
        self._local: dict[str, bool] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self._limiters: dict[str, RateLimiter] = {}
        # The exact ProviderConfig used to build each provider. Lets a live
        # apply_settings() diff old-vs-new per provider and PRESERVE the runtime
        # state (breaker/limiter) of any provider whose config is unchanged.
        self._configs: dict[str, ProviderConfig] = {}
        # The RESOLVED secret per provider (NEVER logged). The config only holds the
        # secret *reference* (keychain:/file:); apply_settings re-resolves on reload
        # and rebuilds a provider whose resolved key changed even when the yaml ref
        # is byte-identical (secret rotation), so a hot-reload picks up a rotated key.
        self._resolved_keys: dict[str, str] = {}
        # E8-S0cost — the ONE shared CostTracker; remembered so providers registered
        # later (mocks, hot additions) still inherit it (single recording site).
        self._cost_tracker: CostTracker | None = None
        # F-multi-tier — round-robin selector for the "which of N healthy
        # providers in this tier" decision (get_with_cascade delegates to it).
        self._tier_selector = TierSelector()

    def set_cost_tracker(self, cost_tracker: CostTracker | None) -> None:
        """Inject the shared CostTracker into every provider (the SINGLE recording
        site, feeding turn_cost_usd); later registrations inherit it via register_mock."""
        self._cost_tracker = cost_tracker
        for provider in self._providers.values():
            inject_cost_tracker(provider, cost_tracker)
        log.engine.debug(
            "[registry] set_cost_tracker: injected into providers",
            extra={"_fields": {"provider_count": len(self._providers), "has_tracker": cost_tracker is not None}},
        )

    @classmethod
    def from_settings(cls, settings: Settings, *, clock: Clock = WallClock()) -> ProviderRegistry:
        """Build a ProviderRegistry from the Settings provider list."""
        registry = cls(clock=clock)
        for config in settings.providers:
            if not config.enabled:
                log.engine.debug(
                    "[registry] provider disabled — skipping",
                    extra={"_fields": {"name": config.name}},
                )
                continue
            log.engine.debug(
                "[registry] constructing provider",
                extra={"_fields": {"name": config.name, "protocol": config.protocol}},
            )
            registry._build_into(
                config,
                clock=registry._clock,
                providers=registry._providers,
                tiers=registry._tiers,
                local=registry._local,
                breakers=registry._breakers,
                limiters=registry._limiters,
                configs=registry._configs,
                resolved_keys=registry._resolved_keys,
            )
        log.engine.info(
            "[registry] init: registry built",
            extra={"_fields": {"provider_count": len(registry._providers)}},
        )
        return registry

    def _build_into(
        self,
        config: ProviderConfig,
        *,
        clock: Clock,
        providers: dict[str, ModelProvider],
        tiers: dict[str, tuple[ModelRoute, ...]],
        local: dict[str, bool],
        breakers: dict[str, CircuitBreaker],
        limiters: dict[str, RateLimiter],
        configs: dict[str, ProviderConfig],
        resolved_keys: dict[str, str],
    ) -> None:
        """Construct one provider (+ breaker/limiter/locality) into the given maps.

        Shared by ``from_settings`` (initial build) and ``apply_settings`` (live
        rebuild of new/changed providers) so the build+register block stays DRY.
        Injects the shared cost tracker so a hot-added provider is also a recording
        site (single cost-recording site invariant).
        """
        from stackowl.config.secret_resolver import SecretResolver

        api_key = SecretResolver.resolve(config.api_key) if config.api_key else ""
        provider = _build_provider(config, api_key)
        inject_cost_tracker(provider, self._cost_tracker)
        providers[config.name] = provider
        if config.tiers:
            routes = [ModelRoute(model=config.default_model, tiers=config.tiers)]
            routes.extend(
                ModelRoute(model=m.name, tiers=m.tiers) for m in config.models
            )
            tiers[config.name] = tuple(routes)
        local[config.name] = is_local_url(config.base_url)
        breakers[config.name] = CircuitBreaker(provider_name=config.name, clock=clock)
        limiters[config.name] = RateLimiter.from_rpm(
            provider_name=config.name,
            rate_limit_rpm=config.rate_limit_rpm,
            clock=clock,
        )
        # F115/SP-4 — wire the breaker the cascade reads into the provider that
        # writes it (per-round, at its HTTP boundary). Without this every breaker
        # stays permanently CLOSED and the cascade can never skip a dead provider.
        _inject_resilience(provider, breakers[config.name], limiters[config.name])
        _inject_cooldown_hours(provider, config.cooldown_hours)
        configs[config.name] = config
        # Remember the RESOLVED secret (value NEVER logged) so a later reload can
        # detect a rotated secret behind an unchanged yaml reference.
        resolved_keys[config.name] = api_key

    def apply_settings(self, settings: Settings) -> None:
        """Rebuild the provider maps from a new Settings, IN PLACE and thread-safe.

        Called on a ``settings_reloaded`` event (from the ConfigWatcher background
        thread) so providers can be added/removed/changed WITHOUT a server restart.

        Thread-safety: builds fresh local dicts and ATOMICALLY swaps the five
        reference dicts at the end (assignment is atomic under the GIL), so a
        concurrent reader on the asyncio loop sees either the fully-old or the
        fully-new registry — never a half-built one. We mutate the SAME registry
        object (callers captured this reference at startup), never rebind it.

        Preservation: a provider whose ProviderConfig is byte-for-byte unchanged
        AND whose underlying secret resolves to the same value keeps its EXISTING
        provider/breaker/limiter objects so circuit-breaker and rate-limiter runtime
        state survives the reload.

        Secret rotation: if the config is unchanged but the secret behind its
        reference (keychain:/file:) was rotated, the provider client is REBUILT with
        the freshly-resolved key while the breaker + limiter are CARRIED OVER (so
        the rotation does not reset circuit/rate state).

        Resilience: a single bad provider config is logged and skipped (a bad
        hot-edit must NOT crash a running server). If the whole rebuild fails, the
        old maps are KEPT (never left half-built).
        """
        from stackowl.config.secret_resolver import SecretResolver
        log.engine.info(
            "[registry] apply_settings: entry",
            extra={"_fields": {"incoming_providers": len(settings.providers)}},
        )
        try:
            new_providers: dict[str, ModelProvider] = {}
            new_tiers: dict[str, tuple[ModelRoute, ...]] = {}
            new_local: dict[str, bool] = {}
            new_breakers: dict[str, CircuitBreaker] = {}
            new_limiters: dict[str, RateLimiter] = {}
            new_configs: dict[str, ProviderConfig] = {}
            new_resolved_keys: dict[str, str] = {}

            added: list[str] = []
            preserved: list[str] = []
            rotated: list[str] = []
            for config in settings.providers:
                if not config.enabled:
                    log.engine.debug(
                        "[registry] apply_settings: provider disabled — skipping",
                        extra={"_fields": {"name": config.name}},
                    )
                    continue
                name = config.name
                # Re-resolve the secret behind the (possibly unchanged) reference so a
                # ROTATED secret is detected even when the yaml ref is byte-identical.
                # A resolve failure (e.g. a momentarily missing secret file) must NOT
                # abort the whole reload — keep the existing provider (transient), or
                # skip a brand-new one. Mirrors the per-provider resilience contract.
                try:
                    new_key = SecretResolver.resolve(config.api_key) if config.api_key else ""
                except Exception as exc:
                    if name in self._providers:
                        new_providers[name] = self._providers[name]
                        new_tiers[name] = self._tiers.get(
                            name,
                            (
                                ModelRoute(model=config.default_model, tiers=config.tiers),
                                *(ModelRoute(model=m.name, tiers=m.tiers) for m in config.models),
                            ),
                        )
                        new_local[name] = self._local[name]
                        new_breakers[name] = self._breakers[name]
                        new_limiters[name] = self._limiters[name]
                        new_configs[name] = self._configs.get(name, config)
                        new_resolved_keys[name] = self._resolved_keys.get(name, "")
                        preserved.append(name)
                        log.engine.warning(
                            "[registry] apply_settings: secret re-resolve failed — "
                            "keeping the existing provider with its current key",
                            exc_info=exc,
                            extra={"_fields": {"name": name}},
                        )
                    else:
                        log.engine.error(
                            "[registry] apply_settings: secret resolve failed for a new "
                            "provider — skipping it this reload",
                            exc_info=exc,
                            extra={"_fields": {"name": name}},
                        )
                    continue
                if name in self._providers and self._configs.get(name) == config:
                    if self._resolved_keys.get(name) == new_key:
                        # FULLY UNCHANGED — config AND resolved secret identical;
                        # preserve provider + runtime state (breaker/limiter).
                        new_providers[name] = self._providers[name]
                        new_tiers[name] = self._tiers.get(
                            name,
                            (
                                ModelRoute(model=config.default_model, tiers=config.tiers),
                                *(ModelRoute(model=m.name, tiers=m.tiers) for m in config.models),
                            ),
                        )
                        new_local[name] = self._local[name]
                        new_breakers[name] = self._breakers[name]
                        new_limiters[name] = self._limiters[name]
                        new_configs[name] = self._configs[name]
                        new_resolved_keys[name] = self._resolved_keys[name]
                        preserved.append(name)
                        continue
                    # SECRET ROTATED — same yaml ref, different resolved value.
                    # Rebuild the (immutable) provider client with the new key but
                    # CARRY OVER breaker + limiter so circuit/rate state survives.
                    try:
                        provider = _build_provider(config, new_key)
                        inject_cost_tracker(provider, self._cost_tracker)
                        new_providers[name] = provider
                        new_tiers[name] = self._tiers.get(
                            name,
                            (
                                ModelRoute(model=config.default_model, tiers=config.tiers),
                                *(ModelRoute(model=m.name, tiers=m.tiers) for m in config.models),
                            ),
                        )
                        new_local[name] = self._local[name]
                        new_breakers[name] = self._breakers[name]
                        new_limiters[name] = self._limiters[name]
                        # SP-4 hot-reload critical: the provider was REBUILT but the
                        # breaker+limiter are CARRIED — re-inject the carried objects
                        # into the new provider, else a rotation silently resets it to
                        # a breaker-less state (asserted in the hot-reload test).
                        _inject_resilience(provider, self._breakers[name], self._limiters[name])
                        _inject_cooldown_hours(provider, config.cooldown_hours)
                        new_configs[name] = config
                        new_resolved_keys[name] = new_key  # value NEVER logged
                        rotated.append(name)
                        log.engine.info(
                            "[registry] apply_settings: secret rotation applied — "
                            "rebuilt provider client, preserved circuit/rate state",
                            extra={"_fields": {"name": name}},
                        )
                    except Exception as exc:
                        log.engine.error(
                            "[registry] apply_settings: skipped provider on secret rotation",
                            exc_info=exc,
                            extra={"_fields": {"name": name}},
                        )
                    continue
                # NEW or CHANGED — build fresh (fresh breaker + limiter).
                try:
                    self._build_into(
                        config,
                        clock=self._clock,
                        providers=new_providers,
                        tiers=new_tiers,
                        local=new_local,
                        breakers=new_breakers,
                        limiters=new_limiters,
                        configs=new_configs,
                        resolved_keys=new_resolved_keys,
                    )
                    added.append(name)
                except Exception as exc:
                    # A bad hot-edit for ONE provider must not crash the server or
                    # abort the whole reload — log loudly and skip just this one.
                    log.engine.error(
                        "[registry] apply_settings: skipped bad provider config",
                        exc_info=exc,
                        extra={"_fields": {"name": name}},
                    )

            removed = [n for n in self._providers if n not in new_providers]

            # ATOMIC SWAP — assign every reference at the end so a concurrent
            # reader never observes a partially-rebuilt registry.
            self._providers = new_providers
            self._tiers = new_tiers
            self._local = new_local
            self._breakers = new_breakers
            self._limiters = new_limiters
            self._configs = new_configs
            self._resolved_keys = new_resolved_keys

            # Drop any memoized context window for providers that were added/changed,
            # rotated, or removed so a new base_url / context_chars (even with an
            # unchanged model id) is re-resolved instead of serving a stale window (F123).
            from stackowl.providers import model_window
            for _name in (*added, *rotated, *removed):
                model_window.invalidate(_name)

            log.engine.info(
                "[registry] apply_settings: exit",
                extra={
                    "_fields": {
                        "added": added,
                        "removed": removed,
                        "preserved": preserved,
                        "rotated": rotated,
                        "provider_count": len(new_providers),
                    }
                },
            )
        except Exception as exc:
            # Whole-rebuild failure: KEEP the old maps (we only swapped at the very
            # end, so self.* are still the old, fully-valid dicts).
            log.engine.error(
                "[registry] apply_settings: reload FAILED — keeping previous registry",
                exc_info=exc,
            )

    def get(self, name: str) -> ModelProvider:
        """Return the named provider or raise ProviderNotFoundError."""
        # Snapshot the dict ref once: apply_settings() swaps it atomically from
        # the watcher thread, so reading it twice could straddle a reload.
        providers = self._providers
        if name not in providers:
            raise ProviderNotFoundError(name)
        return providers[name]

    def get_by_tier(self, tier: str) -> ModelProvider:
        """Return the first provider matching the given tier (config order).

        Falls back to the first available provider when no exact match exists.
        Use get_with_cascade() for circuit-aware tier traversal.
        """
        # Snapshot both dict refs together so a concurrent apply_settings() swap
        # (watcher thread) can't make us index a name absent from _providers.
        providers = self._providers
        tiers = _flatten_routes(self._tiers)
        for name, provider_tiers in tiers.items():
            if tier in provider_tiers and name in providers:
                return providers[name]
        if providers:
            fallback_name = next(iter(providers))
            # Loud, actionable degrade: a requested tier with no provider means
            # the roster is incomplete (e.g. no capable model configured). Never
            # silently substitute — surface it so the operator can add/relabel
            # a provider for this tier.
            log.engine.warning(
                "[providers] get_by_tier: no provider serves this tier — "
                "using the first registered provider (degraded); add or relabel "
                "a provider for this tier to fix routing",
                extra={"_fields": {"requested_tier": tier, "returned": fallback_name}},
            )
            return providers[fallback_name]
        raise ProviderNotFoundError(f"tier:{tier}")

    def get_with_cascade(self, preferred_tier: str) -> ModelProvider:
        """Return first non-OPEN provider starting at preferred_tier.

        Walks tiers in order fast → standard → powerful → local, starting at
        `preferred_tier` and wrapping. Skips providers whose CircuitBreaker is
        OPEN. Raises AllProvidersUnavailableError if every provider is OPEN.
        """
        log.engine.debug(
            "[registry] get_with_cascade: entry",
            extra={"_fields": {"preferred_tier": preferred_tier}},
        )

        if preferred_tier in _TIER_ORDER:
            start = _TIER_ORDER.index(preferred_tier)
            tier_walk: tuple[str, ...] = _TIER_ORDER[start:] + _TIER_ORDER[:start]
        else:
            log.engine.warning(
                "[registry] get_with_cascade: unknown tier — using full order",
                extra={"_fields": {"preferred_tier": preferred_tier}},
            )
            tier_walk = _TIER_ORDER

        # Snapshot the dict refs together: apply_settings() swaps them atomically
        # from the watcher thread. Using locals + .get() means a name iterated
        # from a stale _tiers can never KeyError against a freshly-swapped
        # _providers (the provider is simply skipped if it was removed).
        providers = self._providers
        tiers = self._tiers
        breakers = self._breakers

        details: list[str] = []
        for tier in tier_walk:
            # Every name TIERS assigns to this tier — deliberately NOT filtered by
            # presence in `providers`. This bounds the retry loop below; a name
            # concurrently removed from `providers` must still count toward the
            # bound (it's exactly the case the retry exists for), otherwise a
            # tier with one removed + one still-present provider could exhaust its
            # budget on the removed name alone and never reach the healthy one.
            tier_names = [
                name for name, routes in tiers.items()
                if any(tier in route.tiers for route in routes)
            ]

            chosen: str | None = None
            prov: ModelProvider | None = None
            missing_this_tier: set[str] = set()
            # Bounded by len(tier_names): a name select() returns that is missing
            # from the snapshot (concurrent-removal race) means the round-robin
            # cursor already advanced past it, so retrying WITHIN THE SAME TIER
            # naturally tries the next healthy candidate instead of abandoning the
            # rest of this tier's healthy providers by falling through to the next
            # tier. Capped at len(tier_names) iterations so a fully-stale snapshot
            # (or a single-name tier) can never spin.
            for _ in range(len(tier_names) or 1):
                # TierSelector.select's `providers` param is typed dict[str, object]
                # (it never touches provider internals, only membership) — dict's
                # invariance means dict[str, ModelProvider] needs an explicit cast.
                candidate = self._tier_selector.select(tier, cast("dict[str, object]", providers), tiers, breakers)
                if candidate is None:
                    break
                candidate_prov = providers.get(candidate)
                if candidate_prov is None:
                    missing_this_tier.add(candidate)
                    log.engine.warning(
                        "[cascade] selected provider missing from snapshot "
                        "(concurrent removal) — retrying within the same tier",
                        extra={"_fields": {"provider": candidate, "tier": tier}},
                    )
                    if len(missing_this_tier) >= len(tier_names):
                        break
                    continue
                chosen = candidate
                prov = candidate_prov
                break

            if chosen is not None and prov is not None:
                breaker = breakers.get(chosen)
                state = breaker.state if breaker is not None else None
                log.engine.info(
                    "[cascade] selected '%s' (tier=%s, state=%s)",
                    chosen,
                    tier,
                    state.value if state is not None else "no-breaker",
                    extra={
                        "_fields": {
                            "provider": chosen,
                            "tier": tier,
                            "circuit_state": state.value if state is not None else None,
                        }
                    },
                )
                return prov
            candidates = [
                name for name, routes in tiers.items()
                if any(tier in route.tiers for route in routes) and name in providers
            ]
            if candidates:
                open_names = [
                    name for name in candidates
                    if breakers.get(name) is not None and breakers[name].state is CircuitState.OPEN
                ]
                for name in open_names:
                    msg = f"{name}: skipped (circuit open)"
                    log.engine.info(
                        "[cascade] %s: skipped (circuit open)",
                        name,
                        extra={
                            "_fields": {
                                "provider": name,
                                "tier": tier,
                                "retry_after_seconds": breakers[name].retry_after_seconds,
                            }
                        },
                    )
                    details.append(msg)

        log.engine.error(
            "[registry] get_with_cascade: exit — all providers unavailable",
            extra={"_fields": {"details": details}},
        )
        raise AllProvidersUnavailableError(details)

    def resolve_tier_with_fallback(
        self, tier: str,
    ) -> tuple[ModelProvider, str | None]:
        """Tier resolution that is circuit-aware ONLY when the chosen provider is OPEN.

        Returns ``(provider, degraded_from)``. ``degraded_from`` is the name of the
        provider we fell back FROM (its circuit was OPEN), or ``None`` when no
        fallback occurred. Happy path (chosen provider healthy) is byte-identical
        to :meth:`get_by_tier`; the cascade is only invoked when the chosen
        provider's circuit is OPEN. Raises :class:`AllProvidersUnavailableError`
        if every provider is OPEN (caller floors).
        """
        log.engine.debug(
            "[registry] resolve_tier_with_fallback: entry",
            extra={"_fields": {"tier": tier}},
        )
        providers = self._providers
        tiers = _flatten_routes(self._tiers)
        breakers = self._breakers
        primary_name: str | None = None
        for name, ptiers in tiers.items():
            if tier in ptiers and name in providers:
                primary_name = name
                break
        if primary_name is None:
            log.engine.debug(
                "[registry] resolve_tier_with_fallback: no tier match — config degrade",
                extra={"_fields": {"tier": tier}},
            )
            return self.get_by_tier(tier), None
        breaker = breakers.get(primary_name)
        if breaker is None or breaker.state is not CircuitState.OPEN:
            log.engine.debug(
                "[registry] resolve_tier_with_fallback: exit — healthy primary",
                extra={"_fields": {"tier": tier, "primary": primary_name}},
            )
            return providers[primary_name], None
        log.engine.info(
            "[registry] resolve_tier_with_fallback: primary circuit OPEN — cascading",
            extra={"_fields": {"tier": tier, "degraded_from": primary_name}},
        )
        healthy = self.get_with_cascade(tier)
        return healthy, primary_name

    def resolve_capable_or_degrade(
        self, tier: str,
    ) -> tuple[ModelProvider, str | None]:
        """Resolve a CAPABLE tier, cascading to the most-capable available substitute.

        Returns ``(provider, degraded_from)``. On an exact match ``degraded_from``
        is ``None``. When no provider serves ``tier``, this prefers the next
        MOST-CAPABLE available tier (``_CAPABILITY_ORDER``) and returns
        ``degraded_from=tier`` so the caller can SURFACE the substitution — never a
        silent arbitrary (possibly weak-local) provider as :meth:`get_by_tier` did
        (F125). Used by parliament synthesis, which depends on actually getting a
        powerful model. Raises :class:`ProviderNotFoundError` when the roster is
        empty (no honest substitute exists).
        """
        log.engine.debug(
            "[registry] resolve_capable_or_degrade: entry",
            extra={"_fields": {"tier": tier}},
        )
        # Snapshot together: a concurrent apply_settings() swaps both atomically.
        providers = self._providers
        tiers = _flatten_routes(self._tiers)

        # Exact match first — byte-identical happy path to get_by_tier.
        for name, provider_tiers in tiers.items():
            if tier in provider_tiers and name in providers:
                return providers[name], None

        # No exact provider: cascade by CAPABILITY (most-capable first), skipping the
        # requested tier itself (already known absent). Returns degraded_from=tier.
        for cand_tier in _CAPABILITY_ORDER:
            if cand_tier == tier:
                continue
            for name, provider_tiers in tiers.items():
                if cand_tier in provider_tiers and name in providers:
                    log.engine.warning(
                        "[registry] resolve_capable_or_degrade: no provider for "
                        "requested tier — substituting the most-capable available "
                        "tier (DEGRADED); add/relabel a provider to fix routing",
                        extra={"_fields": {
                            "requested_tier": tier,
                            "substitute_tier": cand_tier,
                            "substitute": name,
                        }},
                    )
                    return providers[name], tier

        log.engine.error(
            "[registry] resolve_capable_or_degrade: no providers registered",
            extra={"_fields": {"tier": tier}},
        )
        raise ProviderNotFoundError(f"tier:{tier}")

    def healthy_distinct(self, limit: int | None = None) -> list[ModelProvider]:
        """Return providers whose CircuitBreaker is NOT OPEN, distinct underlying.

        Used by MoA layer-1 fan-out (E8-S2): a roster of independent, available
        providers. A provider with no breaker counts as healthy. Distinctness is
        by underlying provider identity (``id``) so the same instance registered
        under two names is not consulted twice. ``limit`` caps the roster size.
        """
        log.engine.debug(
            "[registry] healthy_distinct: entry",
            extra={"_fields": {"limit": limit, "total": len(self._providers)}},
        )
        seen: set[int] = set()
        roster: list[ModelProvider] = []
        skipped_open: list[str] = []
        for name, provider in self._providers.items():
            breaker = self._breakers.get(name)
            if breaker is not None and breaker.state is CircuitState.OPEN:
                skipped_open.append(name)
                continue
            identity = id(provider)
            if identity in seen:
                continue
            seen.add(identity)
            roster.append(provider)
            if limit is not None and len(roster) >= limit:
                break
        log.engine.debug(
            "[registry] healthy_distinct: exit",
            extra={"_fields": {"healthy": len(roster), "skipped_open": skipped_open}},
        )
        return roster

    def get_circuit_breaker(self, name: str) -> CircuitBreaker | None:
        """Return the CircuitBreaker for `name`, or None if unknown."""
        return self._breakers.get(name)

    def get_rate_limiter(self, name: str) -> RateLimiter | None:
        """Return the RateLimiter for `name`, or None if unknown."""
        return self._limiters.get(name)

    def register_mock(
        self,
        name: str,
        mock: ModelProvider,
        *,
        tier: str = "fast",
        base_url: str | None = None,
        is_local: bool | None = None,
        models: tuple[ModelRoute, ...] | None = None,
    ) -> None:
        """Register a mock provider — for tests only. Bypasses config lookup.

        ``base_url`` lets a test mirror the shipped config shape so locality is
        inferred exactly as production does; ``is_local`` overrides it explicitly.
        ``models`` lets a test register MULTIPLE (model, tiers) routes under one
        mock provider (per-model provider config testing); when omitted (the
        default, and every existing call site), behaves byte-identically to
        today: one route, model="" (the provider's own default), tier=``tier``.
        """
        self._providers[name] = mock
        self._tiers[name] = models if models is not None else (ModelRoute(model="", tiers=(tier,)),)
        self._local[name] = is_local if is_local is not None else is_local_url(base_url)
        self._breakers[name] = CircuitBreaker(provider_name=name, clock=self._clock)
        self._limiters[name] = RateLimiter.from_rpm(name, None, clock=self._clock)
        inject_cost_tracker(mock, self._cost_tracker)  # E8-S0cost single recording site
        # SP-4 — a mock that subclasses ModelProvider also records onto its breaker
        # via the per-round bracket (the merge-gate journey drives this real path).
        _inject_resilience(mock, self._breakers[name], self._limiters[name])
        log.engine.debug(
            "[registry] mock registered",
            extra={"_fields": {"name": name, "tier": tier, "models": len(self._tiers[name])}},
        )

    def all(self) -> list[ModelProvider]:
        return list(self._providers.values())

    @property
    def contributor_name(self) -> str:
        return "provider_registry"

    async def health_check(self) -> HealthStatus:
        if not self._providers:
            return HealthStatus(
                name=self.contributor_name,
                status="degraded",
                message="no providers",
                latency_ms=0,
            )
        open_breakers = [name for name, breaker in self._breakers.items() if breaker.state is CircuitState.OPEN]
        statuses = await asyncio.gather(
            *(p.health_check() for p in self._providers.values()),
            return_exceptions=True,
        )
        all_ok = all(isinstance(s, HealthStatus) and s.status == "ok" for s in statuses)
        if open_breakers:
            log.engine.warning(
                "[registry] health: open circuits present",
                extra={"_fields": {"open_breakers": open_breakers}},
            )
            return HealthStatus(
                name=self.contributor_name,
                status="degraded",
                message=f"open circuits: {', '.join(open_breakers)}",
                latency_ms=0,
            )
        return HealthStatus(
            name=self.contributor_name,
            status="ok" if all_ok else "degraded",
            message=None,
            latency_ms=0,
        )
