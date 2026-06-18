"""Tests for WebSearchTool (E6-S3).

The tool is a thin wrapper over ``get_services().web_search_registry``. We inject a
fake registry through ``StepServices`` (set_services) so these tests stay network-free
and assert the frozen ``WebSearchResult`` shape flows through unchanged.
"""

from __future__ import annotations

import json

import pytest

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.base import ToolManifest
from stackowl.tools.registry import ToolRegistry
from stackowl.tools.search.web_search import WebSearchTool
from stackowl.web_search.base import (
    WebHit,
    WebSearchResult,
    failure_result,
    success_result,
)


class _FakeRegistry:
    """Stand-in for WebSearchRegistry — records the call and returns a canned result."""

    def __init__(self, result: WebSearchResult) -> None:
        self._result = result
        self.calls: list[tuple[str, int]] = []

    async def search(
        self, query: str, limit: int = 5, *, provider: str | None = None
    ) -> WebSearchResult:
        self.calls.append((query, limit))
        return self._result


def _hit(position: int) -> WebHit:
    return WebHit(
        title=f"Result {position}",
        url=f"https://example.test/{position}",
        description=f"snippet {position}",
        position=position,
    )


def _with_registry(registry: object):  # noqa: ANN202 — context-manager-like token helper
    return set_services(StepServices(web_search_registry=registry))  # type: ignore[arg-type]


# ----------------------------------------------------------------------- manifest


def test_manifest_is_read_and_web_group() -> None:
    m = WebSearchTool().manifest
    assert isinstance(m, ToolManifest)
    assert m.name == "web_search"
    assert m.action_severity == "read"
    assert m.toolset_group == "web"


def test_description_states_lane_and_antilane() -> None:
    d = WebSearchTool().description.lower()
    assert "search" in d
    # ANTI-LANE references the sibling fetch + browser tools.
    assert "web_fetch" in d
    assert "browser" in d


def test_registered_in_with_defaults() -> None:
    tool = ToolRegistry.with_defaults().get("web_search")
    assert tool is not None
    assert isinstance(tool, WebSearchTool)


# ------------------------------------------------------------------------- happy


async def test_happy_returns_frozen_success_shape() -> None:
    fake = _FakeRegistry(success_result([_hit(1), _hit(2)]))
    token = _with_registry(fake)
    try:
        res = await WebSearchTool().execute(query="how to brew coffee", limit=2)
    finally:
        reset_services(token)

    assert res.success
    assert fake.calls == [("how to brew coffee", 2)]
    payload = json.loads(res.output)
    assert payload == {
        "success": True,
        "data": {
            "web": [
                {
                    "title": "Result 1",
                    "url": "https://example.test/1",
                    "description": "snippet 1",
                    "position": 1,
                },
                {
                    "title": "Result 2",
                    "url": "https://example.test/2",
                    "description": "snippet 2",
                    "position": 2,
                },
            ]
        },
    }
    assert "error" not in payload  # success omits error entirely


async def test_default_limit_is_five() -> None:
    fake = _FakeRegistry(success_result([_hit(1)]))
    token = _with_registry(fake)
    try:
        await WebSearchTool().execute(query="anything")
    finally:
        reset_services(token)
    assert fake.calls == [("anything", 5)]


# -------------------------------------------------------------------------- edge


async def test_registry_failure_returns_structured_failure() -> None:
    fake = _FakeRegistry(failure_result("provider 'brave' not configured"))
    token = _with_registry(fake)
    try:
        res = await WebSearchTool().execute(query="quantum widgets")
    finally:
        reset_services(token)

    assert not res.success
    payload = json.loads(res.output)
    assert payload["success"] is False
    assert payload["data"] == {"web": []}
    assert payload["error"] == "provider 'brave' not configured"
    assert res.error == "provider 'brave' not configured"


async def test_registry_none_is_unavailable_structured() -> None:
    token = set_services(StepServices(web_search_registry=None))
    try:
        res = await WebSearchTool().execute(query="anything")
    finally:
        reset_services(token)

    assert not res.success
    payload = json.loads(res.output)
    assert payload["success"] is False
    assert payload["data"] == {"web": []}
    assert "not configured" in payload["error"].lower()


@pytest.mark.parametrize("bad_query", ["", "   ", "\t\n"])
async def test_empty_or_whitespace_query_is_validation_error(bad_query: str) -> None:
    # Even with a working registry, an empty/whitespace query never reaches it.
    fake = _FakeRegistry(success_result([_hit(1)]))
    token = _with_registry(fake)
    try:
        res = await WebSearchTool().execute(query=bad_query)
    finally:
        reset_services(token)

    assert not res.success  # structured, not a raise
    assert fake.calls == []
    payload = json.loads(res.output)
    assert payload["success"] is False
    assert "query" in payload["error"].lower()


async def test_garbage_limit_falls_back_to_default() -> None:
    fake = _FakeRegistry(success_result([_hit(1)]))
    token = _with_registry(fake)
    try:
        await WebSearchTool().execute(query="x", limit="not-a-number")
    finally:
        reset_services(token)
    assert fake.calls == [("x", 5)]


# ------------------------------------------------------------------- integration


async def test_dispatch_via_with_defaults_registry() -> None:
    fake = _FakeRegistry(success_result([_hit(1)]))
    token = _with_registry(fake)
    try:
        tool = ToolRegistry.with_defaults().get("web_search")
        assert tool is not None
        res = await tool.execute(query="integration query", limit=1)
    finally:
        reset_services(token)

    assert res.success
    assert fake.calls == [("integration query", 1)]
    payload = json.loads(res.output)
    assert payload["data"]["web"][0]["url"] == "https://example.test/1"
