"""RegistryAccessorsMixin — name/tier/locality/circuit accessors (B2 split).

Extracted from :class:`stackowl.providers.registry.ProviderRegistry` so registry.py
stays under the B2 line cap. These read-only accessors resolve a ``ModelProvider``
to its registered name (identity match) and look up the per-provider maps the
registry owns. The vision selector (E10-S1) uses ``tier_of`` / ``is_local`` /
``is_open`` to prefer healthy, self-hosted vision backends.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.providers.circuit_breaker import CircuitState

if TYPE_CHECKING:
    from stackowl.providers.base import ModelProvider
    from stackowl.providers.circuit_breaker import CircuitBreaker


class RegistryAccessorsMixin:
    """Read-only provider accessors composed into ``ProviderRegistry``.

    The four maps below are owned + populated by ``ProviderRegistry.__init__``;
    declared here only for the mixin's own type view (no runtime state of its own).
    """

    _providers: dict[str, ModelProvider]
    _tiers: dict[str, str]
    _local: dict[str, bool]
    _breakers: dict[str, CircuitBreaker]

    def _name_of(self, provider: ModelProvider) -> str | None:
        """The registered name for ``provider`` (identity match), or None."""
        for name, prov in self._providers.items():
            if prov is provider:
                return name
        return None

    def tier_of(self, provider: ModelProvider) -> str | None:
        """The configured routing tier, or None if unknown."""
        name = self._name_of(provider)
        return self._tiers.get(name) if name is not None else None

    def is_local(self, provider: ModelProvider) -> bool:
        """True iff a self-hosted (on-box) backend — derived from the base_url host,
        NOT the tier (a local Ollama is tier ``fast``). Unknown → False (cloud)."""
        name = self._name_of(provider)
        return self._local.get(name, False) if name is not None else False

    def is_open(self, provider: ModelProvider) -> bool:
        """True iff this provider's CircuitBreaker is OPEN (unhealthy); no breaker → False."""
        name = self._name_of(provider)
        breaker = self._breakers.get(name) if name is not None else None
        return breaker is not None and breaker.state is CircuitState.OPEN
