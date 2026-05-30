"""E6 SMOKE — web_search tool through the REAL registry + cascade (fake providers).

Drives the genuine path: the registered WebSearchTool reads
get_services().web_search_registry and calls a REAL WebSearchRegistry whose
providers are fakes (no network). Exercises: a normal SearXNG-first result, the
self-healing cascade (primary raises -> secondary answers), and the
all-unavailable structured failure — all through the tool's frozen output shape.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.registry import ToolRegistry
from stackowl.web_search.base import WebHit, WebSearchProvider, WebSearchResult, success_result
from stackowl.web_search.registry import WebSearchRegistry


@contextlib.contextmanager
def _with_registry(registry: WebSearchRegistry | None) -> Iterator[None]:
    """Install a web_search registry into the ambient services for one test.

    set + reset happen in the SAME context (the calling coroutine), so the
    ContextVar token resets cleanly.
    """
    token = set_services(StepServices(web_search_registry=registry))
    try:
        yield
    finally:
        reset_services(token)


class _FakeProvider(WebSearchProvider):
    """A network-free provider with a scriptable verdict."""

    def __init__(self, name: str, *, available: bool = True, mode: str = "hits") -> None:
        self._name = name
        self._available = available
        self._mode = mode  # "hits" | "raise" | "empty"
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return self._available

    async def search(self, query: str, limit: int) -> WebSearchResult:
        self.calls += 1
        if self._mode == "raise":
            raise RuntimeError(f"{self._name} is down")
        if self._mode == "empty":
            return success_result(())
        return success_result(
            [WebHit(title=f"{self._name} hit", url=f"https://{self._name}.test/1",
                    description="d", position=1)]
        )


def _web_search_tool():  # noqa: ANN202
    return ToolRegistry.with_defaults().get("web_search")


async def test_smoke_web_search_returns_ranked_results() -> None:
    # SearXNG-first chain; SearXNG answers.
    registry = WebSearchRegistry(
        [_FakeProvider("searxng"), _FakeProvider("brave"), _FakeProvider("ddg")]
    )
    tool = _web_search_tool()
    assert tool is not None
    with _with_registry(registry):
        result = await tool.execute(query="ARM64 ML inference", limit=5)
    payload = json.loads(result.output)
    assert payload["success"] is True
    assert payload["data"]["web"][0]["url"] == "https://searxng.test/1"  # self-hosted first


async def test_smoke_web_search_cascades_on_failure() -> None:
    # SearXNG raises -> registry retries once -> advances to Brave, which answers.
    searxng = _FakeProvider("searxng", mode="raise")
    brave = _FakeProvider("brave")
    registry = WebSearchRegistry([searxng, brave, _FakeProvider("ddg")])
    tool = _web_search_tool()
    with _with_registry(registry):
        result = await tool.execute(query="self-healing search", limit=5)
    payload = json.loads(result.output)
    assert payload["success"] is True
    assert payload["data"]["web"][0]["url"] == "https://brave.test/1"  # cascaded
    assert searxng.calls == 2  # retried once before advancing


async def test_smoke_web_search_all_unavailable_is_structured() -> None:
    registry = WebSearchRegistry(
        [_FakeProvider("searxng", available=False), _FakeProvider("ddg", available=False)]
    )
    tool = _web_search_tool()
    with _with_registry(registry):
        result = await tool.execute(query="anything", limit=5)
    payload = json.loads(result.output)
    assert payload["success"] is False
    assert payload["data"]["web"] == []
    assert "unavailable" in (payload.get("error", "") + (result.error or "")).lower()


async def test_smoke_web_search_no_registry_is_structured() -> None:
    tool = _web_search_tool()
    with _with_registry(None):  # registry not configured
        result = await tool.execute(query="anything", limit=5)
    payload = json.loads(result.output) if result.output else {"success": False}
    assert payload["success"] is False


async def test_smoke_web_search_empty_query_validation() -> None:
    registry = WebSearchRegistry([_FakeProvider("ddg")])
    tool = _web_search_tool()
    with _with_registry(registry):
        result = await tool.execute(query="   ", limit=5)
    assert result.success is False  # validation failure, structured, not a raise
