"""WebSearchRegistry — provider precedence resolution, self-healing cascade, TTL cache.

Resolution algorithm (a neutral re-expression of a config-precedence + filtered
precedence-walk pattern):

- An explicitly requested provider wins even if unavailable — we return a precise
  "not configured" error rather than silently switching backends (explicit config is a
  deliberate operator choice).
- Otherwise we walk the constructor-ordered precedence list (self-hosted first:
  SearXNG → Brave → DDG), filtered by ``is_available()``. Each availability probe is
  wrapped so a buggy provider cannot abort resolution.
- Self-healing: a provider is retried once ONLY on a cascade-failure signal (the call
  raises, or returns ``success=False``); if it still fails we advance to the next
  available provider. A ``success=True`` result — even with an empty ``web`` list — is a
  TERMINAL answer (the query simply has no hits) and is returned immediately, never
  retried or cascaded past. When every available provider raises / returns
  ``success=False`` (or none is available) we return a structured "unavailable" result —
  never a raised exception.

Successful results WITH at least one hit are cached in-memory by
(query, resolved-provider, limit) with a short TTL and a bounded entry count. The cache
key always carries a SINGLE resolved provider name (never a multi-provider chain string),
so during the precedence walk each candidate is checked/stored under its own key — a
recovered higher-precedence provider is always preferred over a lower-precedence
provider's stale cached answer. Empty (zero-hit) successes are NOT cached, so a transient
"no results" never sticks for the TTL window. The clock is injectable for tests.

port-source: upstream-agent
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable

from stackowl.infra.observability import log
from stackowl.web_search.base import WebSearchProvider, WebSearchResult, failure_result

_DEFAULT_TTL_SECONDS = 900.0
_DEFAULT_MAX_ENTRIES = 256
_DEFAULT_LIMIT = 5
_MAX_LIMIT = 50

# Two distinct exhausted-cascade messages. Conflating them misleads a rate-limited user
# into thinking they have nothing configured. The chosen message names the exact /config
# knob so the guidance is actionable + discoverable.
#
# NOTE: the "not configured" message intentionally still contains the substring
# "unavailable" (in "… is also unavailable") so downstream consumers / smoke checks that
# probe for that token keep matching either branch.
_NOT_CONFIGURED_ERROR = (
    "Web search is not configured. Set up SearXNG (web_search.searxng_base_url) or add a "
    "Brave API key (web_search.brave_api_key) via /config — the keyless DuckDuckGo fallback "
    "is also unavailable."
)
_TRANSIENT_ERROR = (
    "Web search is temporarily unavailable — a provider errored or rate-limited the "
    "request. Try again shortly, or configure a self-hosted SearXNG instance "
    "(web_search.searxng_base_url via /config) for reliable search."
)


class WebSearchRegistry:
    """Ordered registry of web-search providers with cascade + TTL caching."""

    def __init__(
        self,
        providers: list[WebSearchProvider] | None = None,
        *,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers: list[WebSearchProvider] = list(providers or [])
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._time_fn = time_fn
        # key -> (stored_at, result). OrderedDict gives O(1) LRU-ish eviction.
        self._cache: OrderedDict[tuple[str, str, int], tuple[float, WebSearchResult]] = OrderedDict()
        log.tool.debug(
            "web_search.registry.__init__: entry",
            extra={
                "_fields": {
                    "n_providers": len(self._providers),
                    "ttl_seconds": ttl_seconds,
                    "max_entries": max_entries,
                }
            },
        )

    def register(self, provider: WebSearchProvider) -> None:
        """Append a provider at the lowest precedence."""
        self._providers.append(provider)
        log.tool.debug(
            "web_search.registry.register: provider added",
            extra={"_fields": {"provider": provider.name, "n_providers": len(self._providers)}},
        )

    def cache_size(self) -> int:
        """Current number of cached entries (for tests/observability)."""
        return len(self._cache)

    async def search(self, query: str, limit: int = _DEFAULT_LIMIT, *, provider: str | None = None) -> WebSearchResult:
        """Resolve a provider and run a search with self-healing cascade + per-provider caching.

        Walks the available providers in precedence order. For EACH candidate it FIRST
        checks the per-provider cache ``(query, candidate.name, limit)`` — a hit returns
        immediately, naturally preferring the highest-precedence provider with a fresh
        cached answer. On a miss it calls the candidate (retry-once on raise/``success=False``):

        - a ``success=True`` result (even empty ``web``) is a TERMINAL answer → returned
          immediately; non-empty results are cached, empty results are not;
        - a raise / ``success=False`` (after one retry) advances to the next candidate.

        When no provider is available, or every available provider raised / returned
        ``success=False``, a structured "unavailable" failure is returned. Never raises.
        """
        limit = self._clamp_limit(limit)
        log.tool.debug(
            "web_search.registry.search: entry",
            extra={"_fields": {"query_len": len(query), "limit": limit, "requested_provider": provider}},
        )

        chain = self._resolve_chain(provider)
        if not chain:
            # Either an explicit provider that is unknown/unavailable, or nothing available.
            error = self._unresolved_error(provider)
            log.tool.debug(
                "web_search.registry.search: exit — no provider resolved",
                extra={"_fields": {"requested_provider": provider, "success": False}},
            )
            return failure_result(error)

        for prov in chain:
            cache_key = self._cache_key(query, prov.name, limit)
            cached = self._cache_get(cache_key)
            if cached is not None:
                log.tool.debug(
                    "web_search.registry.search: exit — cache hit",
                    extra={"_fields": {"provider": prov.name, "n_results": len(cached.web), "cache_hit": True}},
                )
                return cached

            result = await self._search_with_retry(prov, query, limit)
            if result is not None:
                # Terminal success (possibly empty). Only cache results that have hits so
                # a transient zero-result answer never sticks for the TTL window.
                if result.web:
                    self._cache_put(cache_key, result)
                log.tool.debug(
                    "web_search.registry.search: exit — success",
                    extra={
                        "_fields": {
                            "provider": prov.name,
                            "n_results": len(result.web),
                            "cache_hit": False,
                            "cached": bool(result.web),
                            "success": True,
                        }
                    },
                )
                return result
            log.tool.debug(
                "web_search.registry.search: cascade advance",
                extra={"_fields": {"failed_provider": prov.name}},
            )

        # Reaching here means the chain was non-empty (at least one provider was AVAILABLE)
        # but every available provider raised / returned success=False after retry — a
        # transient/throttled condition, NOT a missing configuration.
        log.tool.debug(
            "web_search.registry.search: exit — all available providers failed",
            extra={"_fields": {"n_tried": len(chain), "success": False, "reason": "transient"}},
        )
        return failure_result(_TRANSIENT_ERROR)

    # ----------------------------------------------------------------- internals

    def _resolve_chain(self, provider: str | None) -> list[WebSearchProvider]:
        """Return the ordered providers to try.

        Explicit provider → that single provider only (and only if available). Else the
        precedence list filtered by a guarded availability probe.
        """
        if provider is not None:
            match = next((p for p in self._providers if p.name == provider), None)
            if match is None or not self._is_available(match):
                return []
            return [match]
        return [p for p in self._providers if self._is_available(p)]

    def _unresolved_error(self, provider: str | None) -> str:
        """Precise structured error when no provider could be resolved.

        An empty chain on the default path means NO provider was available (none
        configured / ddgs not importable) → the "not configured" guidance. An explicitly
        requested-but-unavailable provider keeps its own precise message.
        """
        if provider is not None:
            return f"provider {provider!r} not configured"
        return _NOT_CONFIGURED_ERROR

    def _is_available(self, provider: WebSearchProvider) -> bool:
        """Guarded availability probe — a buggy provider cannot abort resolution."""
        try:
            return provider.is_available()
        except Exception as exc:  # noqa: BLE001 — isolate buggy third-party providers
            log.tool.warning(
                "web_search.registry: is_available raised — treating as unavailable",
                exc_info=exc,
                extra={"_fields": {"provider": provider.name}},
            )
            return False

    async def _search_with_retry(self, provider: WebSearchProvider, query: str, limit: int) -> WebSearchResult | None:
        """Call a provider with bounded retry-once. Return a terminal result or None.

        A ``success=True`` result — even with zero hits — is TERMINAL and returned as-is
        (the query simply has no results; this is not a cascade-failure). Only a raise or
        a ``success=False`` is a cascade-failure: it triggers one retry, then gives up
        (None) so the caller advances to the next provider.
        """
        for attempt in (1, 2):
            result = await self._safe_search(provider, query, limit)
            if result is not None and result.success:
                return result
            log.tool.debug(
                "web_search.registry: provider attempt failed",
                extra={"_fields": {"provider": provider.name, "attempt": attempt}},
            )
        return None

    async def _safe_search(self, provider: WebSearchProvider, query: str, limit: int) -> WebSearchResult | None:
        """Invoke provider.search, converting any raise into None (failure)."""
        try:
            return await provider.search(query, limit)
        except Exception as exc:  # noqa: BLE001 — providers must never crash the cascade
            log.tool.warning(
                "web_search.registry: provider.search raised",
                exc_info=exc,
                extra={"_fields": {"provider": provider.name}},
            )
            return None

    def _clamp_limit(self, limit: object) -> int:
        """Coerce and clamp ``limit`` into ``[1, _MAX_LIMIT]``.

        A non-int / ``None`` / bool limit is coerced to the default (never raises), so
        absurd or malformed limits can neither crash dispatch nor create unbounded
        distinct cache keys.
        """
        if isinstance(limit, bool) or not isinstance(limit, int):
            return _DEFAULT_LIMIT
        return max(1, min(limit, _MAX_LIMIT))

    def _cache_key(self, query: str, provider_name: str, limit: int) -> tuple[str, str, int]:
        """Build the cache key from (query, single-resolved-provider-name, limit).

        The key ALWAYS carries exactly one resolved provider name — never a multi-provider
        chain string — so each provider in the precedence walk owns its own cache entry and
        a recovered higher-precedence provider is never shadowed by a lower-precedence
        provider's stale answer.
        """
        return (query, provider_name, limit)

    def _cache_get(self, key: tuple[str, str, int]) -> WebSearchResult | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        stored_at, result = entry
        if self._time_fn() - stored_at > self._ttl:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return result

    def _cache_put(self, key: tuple[str, str, int], result: WebSearchResult) -> None:
        self._cache[key] = (self._time_fn(), result)
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)  # evict oldest
