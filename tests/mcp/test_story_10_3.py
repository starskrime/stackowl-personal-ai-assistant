"""Tests for Story 10.3 — MCP capability negotiation, hot-add, TTY guard, spectator events."""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from stackowl.config.test_mode import TestModeGuard
from stackowl.mcp.server import McpServer
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeTool(Tool):
    @property
    def name(self) -> str:
        return "fake_tool"

    @property
    def description(self) -> str:
        return "A fake tool for testing"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok", duration_ms=0.0)


def _make_server(registry: ToolRegistry | None = None, global_settings: object = None) -> McpServer:
    """Create McpServer with mcp package mocked out."""
    reg = registry or ToolRegistry()
    with patch("stackowl.mcp.server._wire_handlers"):
        server = McpServer(reg, global_settings=global_settings)
        server._mcp_server = None
    return server


# ---------------------------------------------------------------------------
# TTY Guard tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_stdio_exits_with_code_2_on_tty() -> None:
    """start_stdio() must exit(2) when stdin is a TTY — checked before TestModeGuard."""
    server = _make_server()
    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        with pytest.raises(SystemExit) as exc_info:
            await server.start_stdio()
    assert exc_info.value.code == 2


@pytest.mark.asyncio
async def test_start_stdio_not_tty_proceeds() -> None:
    """start_stdio() proceeds past the TTY check when stdin is not a TTY."""
    server = _make_server()
    # _mcp_server is None so it will log a warning and return after TestModeGuard
    with patch("sys.stdin") as mock_stdin, \
         patch.object(TestModeGuard, "assert_not_test_mode"):
        mock_stdin.isatty.return_value = False
        # Should not raise SystemExit
        await server.start_stdio()


# ---------------------------------------------------------------------------
# negotiate() tests
# ---------------------------------------------------------------------------


def test_negotiate_includes_protocol_version() -> None:
    """negotiate() always returns protocol_version 2024-11-05."""
    server = _make_server()
    result = server.negotiate({})
    assert result["protocol_version"] == "2024-11-05"


def test_negotiate_streaming_is_always_true() -> None:
    """negotiate() always advertises streaming: True."""
    server = _make_server()
    result = server.negotiate({})
    assert result["streaming"] is True


def test_negotiate_tools_list_is_populated() -> None:
    """negotiate() includes registered tools in the tools list."""
    registry = ToolRegistry()
    registry.register(_FakeTool())
    server = _make_server(registry=registry)
    result = server.negotiate({})
    tools = result["tools"]
    assert isinstance(tools, list)
    assert len(tools) == 1
    assert tools[0]["name"] == "fake_tool"
    assert tools[0]["description"] == "A fake tool for testing"


# ---------------------------------------------------------------------------
# register_capability() tests
# ---------------------------------------------------------------------------


def test_register_capability_adds_to_extra() -> None:
    """register_capability() makes the capability available via negotiate()."""
    server = _make_server()
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
    server.register_capability("foo", schema)
    result = server.negotiate({})
    assert result["foo"] == schema


# ---------------------------------------------------------------------------
# register_tool() tests
# ---------------------------------------------------------------------------


def test_register_tool_adds_to_registry() -> None:
    """register_tool() adds the tool to the ToolRegistry."""
    server = _make_server()
    tool = _FakeTool()
    server.register_tool(tool)
    assert server._registry.get("fake_tool") is not None


# ---------------------------------------------------------------------------
# Spectator event tests
# ---------------------------------------------------------------------------


def test_emit_spectator_active_no_bus_is_noop() -> None:
    """_emit_spectator_active() is a no-op when _event_bus is None."""
    server = _make_server()
    assert server._event_bus is None
    # Should not raise
    server._emit_spectator_active("client-1", "TestClient", "stdio")


def test_emit_spectator_disconnected_no_bus_is_noop() -> None:
    """_emit_spectator_disconnected() is a no-op when _event_bus is None."""
    server = _make_server()
    assert server._event_bus is None
    server._emit_spectator_disconnected("client-1")


def test_emit_spectator_active_publishes_event_when_bus_present() -> None:
    """_emit_spectator_active() calls publish() on the event bus when provided."""
    mock_bus = MagicMock()
    server = _make_server()
    server._event_bus = mock_bus
    server._emit_spectator_active("c-1", "MyClient", "sse")
    mock_bus.publish.assert_called_once()
    payload = mock_bus.publish.call_args[0][0]
    assert payload["event"] == "mcp_spectator_active"
    assert payload["client_id"] == "c-1"
    assert payload["client_name"] == "MyClient"
    assert payload["transport"] == "sse"


def test_emit_spectator_disconnected_publishes_event_when_bus_present() -> None:
    """_emit_spectator_disconnected() calls publish() on the event bus when provided."""
    mock_bus = MagicMock()
    server = _make_server()
    server._event_bus = mock_bus
    server._emit_spectator_disconnected("c-2")
    mock_bus.publish.assert_called_once()
    payload = mock_bus.publish.call_args[0][0]
    assert payload["event"] == "mcp_spectator_disconnected"
    assert payload["client_id"] == "c-2"
