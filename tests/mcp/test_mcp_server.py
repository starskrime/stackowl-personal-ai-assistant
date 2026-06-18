"""Tests for McpServer and McpServerSettings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from unittest.mock import patch

from stackowl.config.test_mode import TestModeGuard, TestModeViolation
from stackowl.mcp.server import McpServer
from stackowl.mcp.server_settings import McpServerSettings
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.registry import ToolRegistry


class _FakeTool(Tool):
    @property
    def name(self) -> str:
        return "fake_tool"

    @property
    def description(self) -> str:
        return "A fake tool"

    @property
    def parameters(self) -> dict[str, object]:
        return {}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok", duration_ms=0.0)


def _make_server() -> McpServer:
    """Create McpServer with mcp package mocked out."""
    with patch("stackowl.mcp.server._wire_handlers"):
        try:
            import mcp  # noqa: F401
            server = McpServer(ToolRegistry())
        except ImportError:
            server = McpServer(ToolRegistry())
        server._mcp_server = None  # disable live mcp server object
    return server


def test_mcp_server_can_be_constructed() -> None:
    server = _make_server()
    assert server is not None


def test_mcp_server_list_tools_response_empty_registry() -> None:
    server = _make_server()
    tools = server.list_tools_response()
    assert tools == []


def test_mcp_server_list_tools_response_with_tool() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool())
    with patch("stackowl.mcp.server._wire_handlers"):
        server = McpServer(registry)
        server._mcp_server = None
    tools = server.list_tools_response()
    assert len(tools) == 1
    assert tools[0]["name"] == "fake_tool"


@pytest.mark.asyncio
async def test_start_stdio_raises_in_test_mode() -> None:
    registry = ToolRegistry()
    with patch("stackowl.mcp.server._wire_handlers"):
        server = McpServer(registry)
    TestModeGuard.activate()
    try:
        with pytest.raises(TestModeViolation):
            await server.start_stdio()
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_start_sse_raises_in_test_mode() -> None:
    registry = ToolRegistry()
    with patch("stackowl.mcp.server._wire_handlers"):
        server = McpServer(registry)
    TestModeGuard.activate()
    try:
        with pytest.raises(TestModeViolation):
            await server.start_sse()
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_start_stdio_no_op_when_mcp_server_none() -> None:
    """start_stdio exits cleanly when _mcp_server is None (mcp not installed)."""
    server = _make_server()
    assert server._mcp_server is None
    # Bypass TestModeGuard to reach the None-check branch
    with patch.object(TestModeGuard, "assert_not_test_mode"):
        await server.start_stdio()  # should return without error


def test_server_settings_is_frozen() -> None:
    s = McpServerSettings()
    with pytest.raises((TypeError, ValidationError)):
        s.port = 9999  # type: ignore[misc]


def test_server_settings_default_transport_is_sse() -> None:
    assert McpServerSettings().transport == "sse"


def test_server_settings_default_enabled_is_false() -> None:
    assert McpServerSettings().enabled is False


def test_server_settings_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        McpServerSettings(unknown=True)  # type: ignore[call-arg]
