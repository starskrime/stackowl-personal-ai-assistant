"""Tests for McpToolCache and McpToolDefinition."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.mcp.cache import McpToolCache, McpToolDefinition


def _make_tools(server: str = "srv", count: int = 2) -> list[McpToolDefinition]:
    return [McpToolDefinition(name=f"tool_{i}", description=f"desc {i}", server_name=server) for i in range(count)]


def test_get_returns_none_when_empty() -> None:
    cache = McpToolCache()
    assert cache.get("missing") is None


def test_put_then_get_returns_list() -> None:
    cache = McpToolCache()
    tools = _make_tools()
    cache.put("srv", tools)
    result = cache.get("srv")
    assert result is not None
    assert len(result) == 2


def test_is_stale_when_no_entry() -> None:
    cache = McpToolCache()
    assert cache.is_stale("missing") is True


def test_is_stale_false_immediately_after_put() -> None:
    cache = McpToolCache(ttl_seconds=60.0)
    cache.put("srv", _make_tools())
    assert cache.is_stale("srv") is False


def test_invalidate_removes_entry() -> None:
    cache = McpToolCache()
    cache.put("srv", _make_tools())
    cache.invalidate("srv")
    assert cache.get("srv") is None


def test_invalidate_all_clears() -> None:
    cache = McpToolCache()
    cache.put("a", _make_tools("a"))
    cache.put("b", _make_tools("b"))
    cache.invalidate_all()
    assert cache.get("a") is None
    assert cache.get("b") is None


def test_tool_definition_is_frozen() -> None:
    td = McpToolDefinition(name="t", description="d", server_name="s")
    with pytest.raises((TypeError, ValidationError)):
        td.name = "other"  # type: ignore[misc]


def test_two_servers_are_independent() -> None:
    cache = McpToolCache()
    cache.put("a", _make_tools("a", 1))
    cache.put("b", _make_tools("b", 3))
    assert len(cache.get("a") or []) == 1
    assert len(cache.get("b") or []) == 3


def test_tool_definition_default_input_schema() -> None:
    td = McpToolDefinition(name="t", description="d", server_name="s")
    assert td.input_schema == {}
