"""Concrete web-search providers wired into :class:`WebSearchRegistry`.

Precedence (self-hosted first): SearXNG → Brave → DDG. DDG is the keyless zero-config
floor so web search works out of the box; SearXNG and Brave are configured upgrades.

Every provider:
- has a cheap, **network-free** ``is_available()`` (runs on every tool-list paint);
- returns the frozen :class:`WebSearchResult` shape and NEVER raises out of ``search``
  (any error → :func:`failure_result`);
- logs egress as host+path only (never the query string or any API key).

port-source: upstream-agent (SearXNG GET + result-mapping algorithm)
"""

from __future__ import annotations

from stackowl.web_search.providers.brave import BraveProvider
from stackowl.web_search.providers.ddg import DdgProvider
from stackowl.web_search.providers.searxng import SearxngProvider

__all__ = ["BraveProvider", "DdgProvider", "SearxngProvider"]
