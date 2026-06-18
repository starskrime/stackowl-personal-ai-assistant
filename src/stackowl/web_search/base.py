"""WebSearchProvider ABC + the frozen web-search result contract.

This is the single source of truth for the web-search result shape (ADR-7). Providers
(SearXNG, Brave, DDG) and the registry/tool layers all share these types so downstream
pellet/memory code has exactly one contract to depend on.

The provider contract (a cheap network-free availability probe plus an async ``search``
that never raises out) is a neutral re-expression of a well-known multi-provider search
abstraction.

port-source: upstream-agent
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict


class WebHit(BaseModel):
    """A single web search result row.

    Field order/names are part of the frozen public contract (ADR-7): ``title``,
    ``url``, ``description``, ``position`` (1-based rank).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    url: str
    description: str
    position: int


class WebSearchResult(BaseModel):
    """The frozen web-search result envelope shared by providers, registry, and tool.

    Success::

        {"success": True, "data": {"web": [ {title,url,description,position}, ... ]}}

    Failure / unavailable::

        {"success": False, "data": {"web": []}, "error": "<structured message>"}

    Use :func:`success_result` / :func:`failure_result` to build instances rather than
    constructing directly, so the shape stays consistent everywhere.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    success: bool
    web: tuple[WebHit, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Render the canonical wire shape (the verbatim ADR-7 contract).

        ``error`` is omitted entirely on success so the success payload is exactly
        ``{"success", "data": {"web": [...]}}``.
        """
        payload: dict[str, object] = {
            "success": self.success,
            "data": {"web": [hit.model_dump() for hit in self.web]},
        }
        if not self.success:
            payload["error"] = self.error or ""
        return payload


def success_result(hits: list[WebHit] | tuple[WebHit, ...]) -> WebSearchResult:
    """Build a successful result wrapping ``hits`` (possibly empty)."""
    return WebSearchResult(success=True, web=tuple(hits))


def failure_result(error: str) -> WebSearchResult:
    """Build a failure result with an empty web list and a structured ``error``."""
    return WebSearchResult(success=False, web=(), error=error)


class WebSearchProvider(ABC):
    """Abstract base for a single web-search backend.

    Implementations are thin: a name, a cheap availability check, capability flags, and
    an async ``search``. They are wired into :class:`WebSearchRegistry` in precedence
    order (self-hosted first).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable provider identifier, e.g. ``"searxng"`` / ``"brave"`` / ``"ddg"``."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True when this provider is configured and usable.

        MUST be cheap and **network-free** — this runs on every tool-list paint. Check
        environment/config only; never perform HTTP here.
        """

    def supports_search(self) -> bool:
        """Whether this provider can answer ``search`` queries (default True)."""
        return True

    def supports_extract(self) -> bool:
        """Whether this provider can extract page content (default False)."""
        return False

    @abstractmethod
    async def search(self, query: str, limit: int) -> WebSearchResult:
        """Run a search and return the frozen result shape.

        Implementations SHOULD return :func:`failure_result` on error rather than
        raising; the registry also guards every call (belt-and-suspenders), so a raise
        will not crash resolution.
        """
