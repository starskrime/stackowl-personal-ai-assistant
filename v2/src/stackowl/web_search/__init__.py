"""web_search — multi-provider web search domain package.

A top-level domain package (ADR-12: ``tools/`` depends on ``web_search/``, never the
reverse — nothing here imports from ``tools/``). Exposes the provider contract, the
frozen result shape, and the precedence/cascade registry. Concrete providers (SearXNG,
Brave, DDG) are wired in subsequent stories.
"""

from __future__ import annotations

from stackowl.web_search.base import (
    WebHit,
    WebSearchProvider,
    WebSearchResult,
    failure_result,
    success_result,
)
from stackowl.web_search.registry import WebSearchRegistry

__all__ = [
    "WebHit",
    "WebSearchProvider",
    "WebSearchRegistry",
    "WebSearchResult",
    "failure_result",
    "success_result",
]
