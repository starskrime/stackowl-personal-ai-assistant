"""ProviderRegistry — constructs and holds all ModelProvider instances."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from stackowl.exceptions import AllProvidersUnavailableError, ProviderNotFoundError
from stackowl.health.status import HealthStatus
from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.providers.base import ModelProvider
from stackowl.providers.circuit_breaker import CircuitBreaker, CircuitState
from stackowl.providers.rate_limiter import RateLimiter

if TYPE_CHECKING:
    from stackowl.config.provider import ProviderConfig
    from stackowl.config.settings import Settings


_TIER_ORDER: tuple[str, ...] = ("fast", "standard", "powerful", "local")


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


class ProviderRegistry:
    """Holds ModelProvider references plus per-provider CircuitBreaker and RateLimiter.

    Implements HealthContributor structurally: calls health_check() on all providers.
    Cascade routing (get_with_cascade) selects the first non-OPEN provider across
    tiers fast → standard → powerful → local starting from the preferred tier.
    """

    def __init__(self, *, clock: Clock = WallClock()) -> None:
        self._clock: Clock = clock
        self._providers: dict[str, ModelProvider] = {}
        self._tiers: dict[str, str] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self._limiters: dict[str, RateLimiter] = {}

    @classmethod
    def from_settings(cls, settings: Settings, *, clock: Clock = WallClock()) -> ProviderRegistry:
        """Build a ProviderRegistry from the Settings provider list."""
        from stackowl.config.secret_resolver import SecretResolver

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
            api_key = SecretResolver.resolve(config.api_key) if config.api_key else ""
            provider = _build_provider(config, api_key)
            registry._providers[config.name] = provider
            if hasattr(config, "tier") and config.tier:
                registry._tiers[config.name] = config.tier
            registry._breakers[config.name] = CircuitBreaker(
                provider_name=config.name,
                clock=clock,
            )
            registry._limiters[config.name] = RateLimiter.from_rpm(
                provider_name=config.name,
                rate_limit_rpm=config.rate_limit_rpm,
                clock=clock,
            )
        log.engine.info(
            "[registry] init: registry built",
            extra={"_fields": {"provider_count": len(registry._providers)}},
        )
        return registry

    def get(self, name: str) -> ModelProvider:
        """Return the named provider or raise ProviderNotFoundError."""
        if name not in self._providers:
            raise ProviderNotFoundError(name)
        return self._providers[name]

    def get_by_tier(self, tier: str) -> ModelProvider:
        """Return the first provider matching the given tier (config order).

        Falls back to the first available provider when no exact match exists.
        Use get_with_cascade() for circuit-aware tier traversal.
        """
        for name, provider_tier in self._tiers.items():
            if provider_tier == tier and name in self._providers:
                return self._providers[name]
        if self._providers:
            return next(iter(self._providers.values()))
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

        details: list[str] = []
        for tier in tier_walk:
            for name, provider_tier in self._tiers.items():
                if provider_tier != tier:
                    continue
                breaker = self._breakers.get(name)
                if breaker is None:
                    log.engine.debug(
                        "[cascade] %s: selected (no breaker)",
                        name,
                        extra={"_fields": {"provider": name, "tier": tier}},
                    )
                    log.engine.info(
                        "[cascade] selected '%s' (tier=%s)",
                        name,
                        tier,
                        extra={"_fields": {"provider": name, "tier": tier}},
                    )
                    return self._providers[name]
                state = breaker.state
                if state is CircuitState.OPEN:
                    msg = f"{name}: skipped (circuit open)"
                    log.engine.info(
                        "[cascade] %s: skipped (circuit open)",
                        name,
                        extra={
                            "_fields": {
                                "provider": name,
                                "tier": tier,
                                "retry_after_seconds": breaker.retry_after_seconds,
                            }
                        },
                    )
                    details.append(msg)
                    continue
                log.engine.info(
                    "[cascade] selected '%s' (tier=%s, state=%s)",
                    name,
                    tier,
                    state.value,
                    extra={
                        "_fields": {
                            "provider": name,
                            "tier": tier,
                            "circuit_state": state.value,
                        }
                    },
                )
                return self._providers[name]

        log.engine.error(
            "[registry] get_with_cascade: exit — all providers unavailable",
            extra={"_fields": {"details": details}},
        )
        raise AllProvidersUnavailableError(details)

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
    ) -> None:
        """Register a mock provider — for tests only. Bypasses config lookup."""
        self._providers[name] = mock
        self._tiers[name] = tier
        self._breakers[name] = CircuitBreaker(provider_name=name, clock=self._clock)
        self._limiters[name] = RateLimiter.from_rpm(name, None, clock=self._clock)
        log.engine.debug(
            "[registry] mock registered",
            extra={"_fields": {"name": name, "tier": tier}},
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
