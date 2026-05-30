"""SearxngProvider — search via a self-hosted SearXNG instance (JSON API).

SearXNG is a free, self-hosted, privacy-respecting metasearch engine. It is the
highest-precedence provider (self-hosted first) but only available when an operator has
configured an instance base URL (``web_search.searxng_base_url``). No API key required.

The GET + ``results[]`` mapping is a neutral re-expression of a well-known SearXNG
JSON-API integration: ``GET {base}/search?format=json`` → map ``{title,url,content}``.

port-source: upstream-agent
"""

from __future__ import annotations

import json

import httpx

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
_DEFAULT_LANGUAGE = "en"
# Hard ceiling on the response body we will buffer. A hostile / compromised SearXNG
# instance (or a MITM on a plaintext http URL) could otherwise stream a multi-GB body
# into memory → OOM. 8 MiB is generous for a search-results JSON.
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class SearxngProvider(WebSearchProvider):
    """Web search backed by a configured SearXNG instance.

    Network-free availability: a non-empty base URL. The instance is contacted only
    inside :meth:`search`.
    """

    def __init__(self, base_url: str) -> None:
        # Normalise once: strip whitespace + a single trailing slash so f"{base}/search"
        # never doubles the separator.
        self._base_url = (base_url or "").strip().rstrip("/")

    @property
    def name(self) -> str:
        return "searxng"

    def is_available(self) -> bool:
        """True when a SearXNG base URL is configured (cheap, network-free)."""
        return bool(self._base_url)

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    async def search(self, query: str, limit: int) -> WebSearchResult:
        """GET the SearXNG JSON API and map ``results[]`` into the frozen shape."""
        log.tool.debug(
            "web_search.searxng.search: entry",
            extra={"_fields": {"query_len": len(query), "limit": limit}},
        )
        if not self._base_url:
            return failure_result("searxng base_url is not configured")

        url = f"{self._base_url}/search"
        params = {
            "q": query,
            "format": "json",
            "categories": "general",
            "language": _DEFAULT_LANGUAGE,
        }
        try:
            # follow_redirects is explicitly False (defence-in-depth: a future httpx
            # default flip must not silently enable SSRF-amplifying redirect chases). A
            # 3xx then surfaces as a non-2xx that raise_for_status rejects → failure_result.
            async with httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=False) as client:
                log.tool.debug(
                    "web_search.searxng.search: request",
                    extra={"_fields": {"egress": egress_target(url)}},
                )
                async with client.stream(
                    "GET", url, params=params, headers={"Accept": "application/json"}
                ) as resp:
                    resp.raise_for_status()
                    body = b""
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) > _MAX_RESPONSE_BYTES:
                            return failure_result("searxng response too large")
                data = json.loads(body)
        except httpx.HTTPStatusError as exc:
            return failure_result(f"searxng HTTP {exc.response.status_code}")
        except httpx.HTTPError as exc:
            return failure_result(f"searxng request failed: {exc!r}")
        except (ValueError, TypeError) as exc:
            # JSON decode / unexpected payload type.
            return failure_result(f"searxng response parse failed: {exc!r}")

        raw_results = data.get("results", []) if isinstance(data, dict) else []
        hits: list[WebHit] = []
        for i, row in enumerate(raw_results[:limit], start=1):
            if not isinstance(row, dict):
                continue
            hits.append(
                WebHit(
                    title=str(row.get("title", "")),
                    url=str(row.get("url", "")),
                    description=str(row.get("content", "")),
                    position=i,
                )
            )

        log.tool.debug(
            "web_search.searxng.search: exit",
            extra={"_fields": {"success": True, "n_results": len(hits)}},
        )
        return success_result(hits)
