"""Web-search TOOL surface (ADR-12).

The ``web_search`` tool is a thin pipeline-facing wrapper that DEPENDS ON the
``web_search/`` package (provider registry + frozen result contract) — never the
reverse. It reaches the registry at execute time via
``get_services().web_search_registry`` so no construction-time wiring is needed in
the tool registry.
"""

from __future__ import annotations

from stackowl.tools.search.web_search import WebSearchTool

__all__ = ["WebSearchTool"]
