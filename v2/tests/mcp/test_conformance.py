"""MCP conformance tests — validates MCP contract without a live server (integration)."""

from __future__ import annotations

import pytest
from unittest.mock import patch

from stackowl.mcp.server import McpServer
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.registry import ToolRegistry

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ConformanceTool(Tool):
    @property
    def name(self) -> str:
        return "conformance_tool"

    @property
    def description(self) -> str:
        return "Tool for MCP conformance testing"

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The query to run"}
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok", duration_ms=0.0)


def _make_conformance_server() -> McpServer:
    registry = ToolRegistry()
    registry.register(_ConformanceTool())
    with patch("stackowl.mcp.server._wire_handlers"):
        server = McpServer(registry)
        server._mcp_server = None
    return server


# ---------------------------------------------------------------------------
# Conformance tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_protocol_version_in_negotiate_response() -> None:
    """negotiate() must return protocol_version 2024-11-05 per MCP spec."""
    server = _make_conformance_server()
    response = server.negotiate({})
    assert "protocol_version" in response, "negotiate() must include protocol_version"
    assert response["protocol_version"] == "2024-11-05", (
        f"Expected protocol_version '2024-11-05', got {response['protocol_version']!r}"
    )


@pytest.mark.asyncio
async def test_tools_list_response_has_required_fields() -> None:
    """Every tool in list_tools_response() must have name, description, inputSchema."""
    server = _make_conformance_server()
    tools = server.list_tools_response()
    assert len(tools) >= 1, "Expected at least one tool in the registry"
    for tool in tools:
        assert "name" in tool, f"Tool missing 'name': {tool}"
        assert "description" in tool, f"Tool missing 'description': {tool}"
        assert "inputSchema" in tool, f"Tool missing 'inputSchema': {tool}"
        assert isinstance(tool["name"], str) and tool["name"], "Tool name must be a non-empty string"
        assert isinstance(tool["description"], str), "Tool description must be a string"
        assert isinstance(tool["inputSchema"], dict), "Tool inputSchema must be a dict"


@pytest.mark.asyncio
async def test_negotiate_streaming_capability_advertised() -> None:
    """negotiate() must advertise streaming capability as per MCP protocol."""
    server = _make_conformance_server()
    response = server.negotiate({})
    assert "streaming" in response, "negotiate() must include 'streaming' capability"
    assert response["streaming"] is True, "streaming capability must be True"


@pytest.mark.asyncio
async def test_negotiate_tools_matches_list_tools_response() -> None:
    """Tools advertised in negotiate() must match list_tools_response() names."""
    server = _make_conformance_server()
    negotiate_tools = server.negotiate({})["tools"]
    list_response_tools = server.list_tools_response()
    negotiate_names = {t["name"] for t in negotiate_tools}
    list_names = {t["name"] for t in list_response_tools}  # type: ignore[index]
    assert negotiate_names == list_names, (
        f"negotiate() tools {negotiate_names!r} do not match list_tools_response() {list_names!r}"
    )
