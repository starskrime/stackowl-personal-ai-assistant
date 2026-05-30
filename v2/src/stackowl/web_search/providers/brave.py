"""BraveProvider — search via the Brave Search API (keyed, configured upgrade).

Brave Search is a paid/keyed web-search API. It is a configured upgrade (precedence
below SearXNG, above DDG) and is only available when an API-key reference is configured.

The constructor takes a *secret reference* (an env-var name, ``keychain:<service>``, or
``file:<path>`` — whatever :class:`SecretResolver` understands), NOT the raw key. The key
is resolved lazily and cached. Resolving a secret is a local config read (env/keychain/
file), so :meth:`is_available` stays network-free. The key is sent as the
``X-Subscription-Token`` header and is NEVER logged.
"""

from __future__ import annotations

import json

import httpx

from stackowl.config.secret_resolver import SecretResolver
from stackowl.exceptions import ConfigurationError
from stackowl.infra.observability import log
from stackowl.web_search.base import (
    WebHit,
    WebSearchProvider,
    WebSearchResult,
    failure_result,
    success_result,
)
from stackowl.web_search.providers._egress import egress_target

_TIMEOUT_S = 10.0
_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
# Hard ceiling on the response body we will buffer — a hostile / compromised endpoint
# returning a multi-GB body would otherwise be read fully into memory → OOM. 8 MiB is
# generous for a search-results JSON.
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class BraveProvider(WebSearchProvider):
    """Web search backed by the Brave Search API.

    ``key_ref`` is a SecretResolver reference (env-var name / ``keychain:`` / ``file:``).
    When it is empty or unresolvable, the provider reports itself unavailable so the
    registry cascades past it — :meth:`search` is then never reached.
    """

    def __init__(self, key_ref: str | None) -> None:
        self._key_ref = (key_ref or "").strip()
        # Cache the resolution so the (network-free) keychain/env read happens at most
        # once per process rather than on every tool-list paint.
        self._resolved_key: str | None = None
        self._resolution_attempted = False

    @property
    def name(self) -> str:
        return "brave"

    def is_available(self) -> bool:
        """True when an API key is configured and resolvable (cheap, network-free)."""
        return self._resolve_key() is not None

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    def _resolve_key(self) -> str | None:
        """Resolve (and cache) the API key from its reference. Never raises, never logs the value."""
        if self._resolution_attempted:
            return self._resolved_key
        self._resolution_attempted = True
        if not self._key_ref:
            return None
        try:
            self._resolved_key = SecretResolver.resolve(self._key_ref)
        except ConfigurationError as exc:
            log.tool.debug(
                "web_search.brave: api key reference unresolved — provider unavailable",
                extra={"_fields": {"reason": str(exc)}},
            )
            self._resolved_key = None
        return self._resolved_key

    async def search(self, query: str, limit: int) -> WebSearchResult:
        """GET the Brave web-search endpoint and map ``web.results[]`` into the frozen shape."""
        log.tool.debug(
            "web_search.brave.search: entry",
            extra={"_fields": {"query_len": len(query), "limit": limit}},
        )
        key = self._resolve_key()
        if key is None:
            return failure_result("brave api key is not configured")

        params: dict[str, str | int] = {"q": query, "count": limit}
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": key,  # NEVER logged; redacted by SensitiveFieldFilter regardless
        }
        try:
            # follow_redirects is explicitly False (defence-in-depth: a future httpx
            # default flip must not silently enable SSRF-amplifying redirect chases). A
            # 3xx then surfaces as a non-2xx that raise_for_status rejects → failure_result.
            async with httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=False) as client:
                log.tool.debug(
                    "web_search.brave.search: request",
                    extra={"_fields": {"egress": egress_target(_ENDPOINT)}},
                )
                async with client.stream("GET", _ENDPOINT, params=params, headers=headers) as resp:
                    resp.raise_for_status()
                    body = b""
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) > _MAX_RESPONSE_BYTES:
                            return failure_result("brave response too large")
                data = json.loads(body)
        except httpx.HTTPStatusError as exc:
            return failure_result(f"brave HTTP {exc.response.status_code}")
        except httpx.HTTPError as exc:
            return failure_result(f"brave request failed: {exc!r}")
        except (ValueError, TypeError) as exc:
            return failure_result(f"brave response parse failed: {exc!r}")

        web = data.get("web") if isinstance(data, dict) else None
        raw_results = web.get("results", []) if isinstance(web, dict) else []
        hits: list[WebHit] = []
        for i, row in enumerate(raw_results[:limit], start=1):
            if not isinstance(row, dict):
                continue
            hits.append(
                WebHit(
                    title=str(row.get("title", "")),
                    url=str(row.get("url", "")),
                    description=str(row.get("description", "")),
                    position=i,
                )
            )

        log.tool.debug(
            "web_search.brave.search: exit",
            extra={"_fields": {"success": True, "n_results": len(hits)}},
        )
        return success_result(hits)
