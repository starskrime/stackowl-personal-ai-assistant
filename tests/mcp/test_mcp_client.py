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


# --- F-82 (S1): failed/blocked MCP calls must NOT masquerade as empty success ---


@pytest.mark.asyncio
async def test_call_tool_blocked_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A call to a server not in the allowlist raises a typed 'blocked' error."""
    from stackowl.mcp.client import McpCallError

    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", lambda op: None)
    client, config = _client(allowed=False)  # not in allowlist
    with pytest.raises(McpCallError) as exc_info:
        await client.call_tool(config, "my_tool", {})
    assert exc_info.value.kind == "blocked"


@pytest.mark.asyncio
async def test_call_tool_transport_failure_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A session/connection failure raises a typed 'transport' error, not ''."""
    from stackowl.mcp.client import McpCallError

    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", lambda op: None)
    client, config = _client(allowed=True)

    async def _boom(*_a: object, **_k: object) -> str:
        raise ConnectionError("server down")

    monkeypatch.setattr(client, "_invoke_once", _boom)
    with pytest.raises(McpCallError) as exc_info:
        await client.call_tool(config, "my_tool", {})
    assert exc_info.value.kind == "transport"


@pytest.mark.asyncio
async def test_mcp_tool_execute_surfaces_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """McpTool.execute returns success=False with an error on transport failure."""
    from stackowl.mcp.client import McpCallError

    client, config = _client(allowed=True)
    defn = McpToolDefinition(name="my_tool", description="d", server_name="test_srv")
    tool = McpTool(defn, client, config)

    async def _fail(*_a: object, **_k: object) -> str:
        raise McpCallError("transport", "server down")

    monkeypatch.setattr(client, "call_tool", _fail)
    result = await tool.execute()
    assert result.success is False
    assert result.output == ""
    assert result.error and "server down" in result.error


@pytest.mark.asyncio
async def test_mcp_tool_execute_surfaces_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blocked (allowlist-denied) call surfaces as success=False, not empty success."""
    from stackowl.mcp.client import McpCallError

    client, config = _client(allowed=True)
    defn = McpToolDefinition(name="my_tool", description="d", server_name="test_srv")
    tool = McpTool(defn, client, config)

    async def _blocked(*_a: object, **_k: object) -> str:
        raise McpCallError("blocked", "server not in allowlist")

    monkeypatch.setattr(client, "call_tool", _blocked)
    result = await tool.execute()
    assert result.success is False
    assert result.error and "blocked" in result.error.lower()


@pytest.mark.asyncio
async def test_mcp_tool_execute_preserves_genuine_empty_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuinely empty-but-successful tool result stays success=True, output=''."""
    client, config = _client(allowed=True)
    defn = McpToolDefinition(name="my_tool", description="d", server_name="test_srv")
    tool = McpTool(defn, client, config)

    async def _empty(*_a: object, **_k: object) -> str:
        return ""

    monkeypatch.setattr(client, "call_tool", _empty)
    result = await tool.execute()
    assert result.success is True
    assert result.output == ""
