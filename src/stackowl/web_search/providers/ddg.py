"""DdgProvider — keyless DuckDuckGo search (the zero-config floor).

DDG is the lowest-precedence provider but the only one available with no configuration
at all, so web search works out of the box. It is backed by the OSS ``ddgs`` library
(a pure-Python, self-hosted-friendly client — no API key, no vendor lock-in).

``ddgs`` is synchronous, so :meth:`search` runs it in a worker thread via
``asyncio.to_thread`` under an overall timeout. Availability is a network-free importable
check, cached so the import is attempted at most once.

DDG result rows use the keys ``title`` / ``href`` / ``body``; these map to
:class:`WebHit` ``title`` / ``url`` / ``description``.
"""

from __future__ import annotations

import asyncio
import importlib.util
from typing import Any

from stackowl.infra.observability import log
from stackowl.web_search.base import (
    WebHit,
    WebSearchProvider,
    WebSearchResult,
    failure_result,
    success_result,
)

_TIMEOUT_S = 10.0
_MODULE = "ddgs"

# Tri-state import cache: None = not yet probed, True/False = result. Network-free.
_module_available: bool | None = None


class DdgProvider(WebSearchProvider):
    """Keyless web search backed by the OSS ``ddgs`` library."""

    @property
    def name(self) -> str:
        return "ddg"

    def is_available(self) -> bool:
        """True when the ``ddgs`` module is importable (cheap, network-free, cached)."""
        global _module_available
        if _module_available is None:
            _module_available = importlib.util.find_spec(_MODULE) is not None
        return _module_available

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    async def search(self, query: str, limit: int) -> WebSearchResult:
        """Run the synchronous ``ddgs`` text search in a thread, then map results."""
        log.tool.debug(
            "web_search.ddg.search: entry",
            extra={"_fields": {"query_len": len(query), "limit": limit}},
        )
        if not self.is_available():
            return failure_result("ddg backend (ddgs) is not installed")

        try:
            log.tool.debug(
                "web_search.ddg.search: request",
                extra={"_fields": {"egress": "ddgs:text"}},
            )
            # Known ddgs limitation: wait_for cancels the AWAIT, but the synchronous
            # ddgs call running in the worker thread cannot be interrupted and may
            # outlive this timeout (the thread keeps running until ddgs returns).
            raw_results = await asyncio.wait_for(
                asyncio.to_thread(self._run_search, query, limit),
                timeout=_TIMEOUT_S,
            )
        except TimeoutError:
            return failure_result(f"ddg search timed out after {_TIMEOUT_S:.0f}s")
        except Exception as exc:  # noqa: BLE001 — never raise out of a provider
            return failure_result(f"ddg search failed: {exc!r}")

        hits: list[WebHit] = []
        for i, row in enumerate(raw_results[:limit], start=1):
            if not isinstance(row, dict):
                continue
            hits.append(
                WebHit(
                    title=str(row.get("title", "")),
                    url=str(row.get("href", "")),
                    description=str(row.get("body", "")),
                    position=i,
                )
            )

        log.tool.debug(
            "web_search.ddg.search: exit",
            extra={"_fields": {"success": True, "n_results": len(hits)}},
        )
        return success_result(hits)

    def _run_search(self, query: str, limit: int) -> list[dict[str, Any]]:
        """Synchronous ddgs call — runs inside a worker thread."""
        from ddgs import DDGS

        with DDGS() as ddgs_client:
            results = ddgs_client.text(query, max_results=limit)
        return list(results)
