"""Tests for McpClient and McpTool."""

from __future__ import annotations

import pytest

from stackowl.config.test_mode import TestModeGuard, TestModeViolation
from stackowl.mcp._tool import McpTool
from stackowl.mcp.allowlist import McpServerAllowlist, McpServerConfig
from stackowl.mcp.cache import McpToolCache, McpToolDefinition
from stackowl.mcp.client import McpClient
from stackowl.mcp.probe import McpLivenessProbe
from stackowl.tools.registry import ToolRegistry


def _client(allowed: bool = False) -> tuple[McpClient, McpServerConfig]:
    prefixes = ["sse://"] if allowed else []
    allowlist = McpServerAllowlist(prefixes)
    cache = McpToolCache()
    probe = McpLivenessProbe()
    client = McpClient(allowlist, cache, probe)
    config = McpServerConfig(name="test_srv", uri="sse://http://localhost:9999/sse")
    return client, config


@pytest.mark.asyncio
async def test_discover_tools_raises_in_test_mode() -> None:
    client, config = _client(allowed=True)
    TestModeGuard.activate()
    try:
        with pytest.raises(TestModeViolation):
            await client.discover_tools(config)
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_call_tool_raises_in_test_mode() -> None:
    client, config = _client(allowed=True)
    TestModeGuard.activate()
    try:
        with pytest.raises(TestModeViolation):
            await client.call_tool(config, "my_tool", {})
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_discover_tools_returns_empty_for_disallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", lambda op: None)
    client, config = _client(allowed=False)  # not in allowlist
    result = await client.discover_tools(config)
    assert result == []


@pytest.mark.asyncio
async def test_discover_tools_returns_cached_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", lambda op: None)
    client, config = _client(allowed=True)
    cached_tools = [McpToolDefinition(name="cached_tool", description="desc", server_name="test_srv")]
    client._cache.put("test_srv", cached_tools)
    result = await client.discover_tools(config)
    assert len(result) == 1
    assert result[0].name == "cached_tool"


@pytest.mark.asyncio
async def test_register_server_tools_returns_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", lambda op: None)
    client, config = _client(allowed=True)
    tools = [
        McpToolDefinition(name="t1", description="d1", server_name="test_srv"),
        McpToolDefinition(name="t2", description="d2", server_name="test_srv"),
    ]
    client._cache.put("test_srv", tools)
    registry = ToolRegistry()
    count = await client.register_server_tools(config, registry)
    assert count == 2


@pytest.mark.asyncio
async def test_register_server_tools_adds_to_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", lambda op: None)
    client, config = _client(allowed=True)
    client._cache.put("test_srv", [McpToolDefinition(name="my_tool", description="d", server_name="test_srv")])
    registry = ToolRegistry()
    await client.register_server_tools(config, registry)
    # E1-S3: federated tools register under the namespaced key mcp.<server>.<tool>
    assert registry.get("mcp.test_srv.my_tool") is not None


def test_mcp_tool_name_is_namespaced() -> None:
    client, config = _client()
    defn = McpToolDefinition(name="foo", description="bar", server_name="srv")
    tool = McpTool(defn, client, config)
    # E1-S3 / §17: StackOwl-facing name is namespaced (non-clobbering)
    assert tool.name == "mcp.srv.foo"


def test_mcp_tool_description_matches_definition() -> None:
    client, config = _client()
    defn = McpToolDefinition(name="foo", description="bar baz", server_name="srv")
    tool = McpTool(defn, client, config)
    assert tool.description == "bar baz"
