"""WebSearchTool — search the web for a query and return ranked results.

This is the SEARCH primitive: given a free-text query it returns a ranked list of
hits (title / url / snippet) via the provider cascade (SearXNG → Brave → DDG). It
DEPENDS ON the ``web_search/`` package (ADR-12): the tool resolves the registry at
execute time through ``get_services().web_search_registry`` and never constructs
providers itself.

The tool is read-severity (ungated) and self-healing: a missing registry or a
provider cascade failure comes back as the frozen ``WebSearchResult`` failure shape
rather than a raised exception.
"""

from __future__ import annotations

import json
import time

from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.web_search.base import WebSearchResult, failure_result

_DEFAULT_LIMIT = 5
_TOOLSET_GROUP = "web"


class WebSearchTool(Tool):
    """Search the web for a query and return ranked results (title/url/snippet)."""

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "SEARCH the web for a free-text query and return ranked results "
            "(title, url, snippet) from the configured search providers. "
            "A success:true result with an EMPTY 'web' list means the search ran "
            "but found nothing — tell the user no results were found and offer to "
            "rephrase; do NOT invent an answer. A success:false result carries an "
            "actionable 'error' (e.g. rate-limited, or not configured) — relay it. "
            "ANTI-LANE: to FETCH the content of a KNOWN url use web_fetch; "
            "this tool does NOT drive an interactive browser (use the browser_* tools "
            "for clicking/typing/screenshots)."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The free-text search query.",
                },
                "limit": {
                    "type": "integer",
                    "default": _DEFAULT_LIMIT,
                    "minimum": 1,
                    "description": "Maximum number of ranked results to return.",
                },
            },
            "required": ["query"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            toolset_group=_TOOLSET_GROUP,
            capability_tag="web_knowledge",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        # 1. ENTRY
        raw_query = kwargs.get("query", "")
        query = str(raw_query).strip()
        limit = self._coerce_limit(kwargs.get("limit"))
        log.tool.info(
            "web_search.execute: entry",
            extra={"_fields": {"query_len": len(query), "limit": limit}},
        )
        t0 = time.monotonic()

        # 2. DECISION — validate the query (structured error, never a raise).
        if not query:
            log.tool.debug("web_search.execute: empty query — validation error")
            return self._render(
                failure_result("query must be a non-empty string"), t0, success_floor=False
            )

        # 3. STEP — resolve the registry from services; self-heal if unavailable.
        registry = get_services().web_search_registry
        if registry is None:
            log.tool.warning("web_search.execute: registry not configured — unavailable")
            return self._render(
                failure_result("web search unavailable (not configured)"),
                t0,
                success_floor=False,
            )

        result = await registry.search(query, limit)

        # 4. EXIT — both success and failure come back structured.
        log.tool.info(
            "web_search.execute: exit",
            extra={
                "_fields": {
                    "success": result.success,
                    "n_results": len(result.web),
                    "duration_ms": (time.monotonic() - t0) * 1000,
                }
            },
        )
        return self._render(result, t0, success_floor=result.success)

    @staticmethod
    def _coerce_limit(raw: object) -> int:
        """Coerce the optional ``limit`` arg to a positive int, defaulting on garbage."""
        if not isinstance(raw, (int, str)) or isinstance(raw, bool):
            return _DEFAULT_LIMIT
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return _DEFAULT_LIMIT
        return value if value > 0 else _DEFAULT_LIMIT

    @staticmethod
    def _render(result: WebSearchResult, t0: float, *, success_floor: bool) -> ToolResult:
        """Wrap the frozen ``WebSearchResult`` dict into a ToolResult.

        The frozen ``.to_dict()`` payload is serialized into ``output`` (matching how
        other read tools, e.g. search_files, return structured JSON), keeping the
        canonical shape available to downstream consumers. ``error`` is mirrored onto
        the ToolResult on failure for terse surfacing.
        """
        payload = result.to_dict()
        duration_ms = (time.monotonic() - t0) * 1000
        output = json.dumps(payload, ensure_ascii=False)
        error = None if success_floor else str(payload.get("error", ""))
        return ToolResult(
            success=success_floor, output=output, error=error, duration_ms=duration_ms
        )
