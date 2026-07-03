"""Tests for McpClient HealableResource implementation and McpHealthContributor."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stackowl.mcp.allowlist import McpServerAllowlist, McpServerConfig
from stackowl.mcp.cache import McpToolCache
from stackowl.mcp.client import McpClient
from stackowl.mcp.probe import McpLivenessProbe


class TestMcpClientHealable:
    """Test McpClient's no-op HealableResource implementation."""

    def test_available_always_true(self) -> None:
        """McpClient.available should always be True (stateless per-call)."""
        allowlist = McpServerAllowlist([])
        cache = McpToolCache()
        probe = McpLivenessProbe()
        client = McpClient(allowlist, cache, probe)
        assert client.available is True

    def test_unavailable_reason_always_none(self) -> None:
        """McpClient.unavailable_reason should always be None (no persistent state)."""
        allowlist = McpServerAllowlist([])
        cache = McpToolCache()
        probe = McpLivenessProbe()
        client = McpClient(allowlist, cache, probe)
        assert client.unavailable_reason is None

    @pytest.mark.asyncio
    async def test_ensure_available_is_noop(self) -> None:
        """McpClient.ensure_available() should be a no-op (doesn't raise, does nothing)."""
        allowlist = McpServerAllowlist([])
        cache = McpToolCache()
        probe = McpLivenessProbe()
        client = McpClient(allowlist, cache, probe)
        # Should not raise and should complete immediately
        await client.ensure_available()

    def test_register_on_recycled_noop(self, caplog: pytest.LogCaptureFixture) -> None:
        """McpClient.register_on_recycled() should log and do nothing."""
        import logging
        caplog.set_level(logging.DEBUG)

        allowlist = McpServerAllowlist([])
        cache = McpToolCache()
        probe = McpLivenessProbe()
        client = McpClient(allowlist, cache, probe)

        callback = MagicMock()
        client.register_on_recycled(callback)

        # Should have logged a debug message
        assert any("no-op" in rec.message.lower() for rec in caplog.records if "register_on_recycled" in rec.message)
        # Callback should never have been called
        callback.assert_not_called()


class TestMcpHealthContributor:
    """Test McpHealthContributor health check mapping."""

    @pytest.mark.asyncio
    async def test_health_check_all_alive(self) -> None:
        """McpHealthContributor.health_check() should return 'ok' when all servers are alive."""
        from stackowl.health.contributors import McpHealthContributor

        probe = AsyncMock(spec=McpLivenessProbe)
        probe.probe_all.return_value = {
            "server1": True,
            "server2": True,
        }

        contributor = McpHealthContributor(
            probe=probe,
            configs=[
                McpServerConfig(name="server1", uri="sse://localhost:3001", timeout_seconds=5.0),
                McpServerConfig(name="server2", uri="stdio:///path/to/server", timeout_seconds=5.0),
            ],
        )

        status = await contributor.health_check()
        assert status.name == "mcp"
        assert status.status == "ok"
        assert "alive" in status.message.lower() if status.message else True

    @pytest.mark.asyncio
    async def test_health_check_one_down(self) -> None:
        """McpHealthContributor.health_check() should return 'degraded' when one server is down."""
        from stackowl.health.contributors import McpHealthContributor

        probe = AsyncMock(spec=McpLivenessProbe)
        probe.probe_all.return_value = {
            "server1": True,
            "server2": False,
        }

        contributor = McpHealthContributor(
            probe=probe,
            configs=[
                McpServerConfig(name="server1", uri="sse://localhost:3001", timeout_seconds=5.0),
                McpServerConfig(name="server2", uri="stdio:///path/to/server", timeout_seconds=5.0),
            ],
        )

        status = await contributor.health_check()
        assert status.name == "mcp"
        assert status.status == "degraded"
        assert "server2" in status.message if status.message else False

    @pytest.mark.asyncio
    async def test_health_check_all_down(self) -> None:
        """McpHealthContributor.health_check() should return 'down' when all servers are down."""
        from stackowl.health.contributors import McpHealthContributor

        probe = AsyncMock(spec=McpLivenessProbe)
        probe.probe_all.return_value = {
            "server1": False,
            "server2": False,
        }

        contributor = McpHealthContributor(
            probe=probe,
            configs=[
                McpServerConfig(name="server1", uri="sse://localhost:3001", timeout_seconds=5.0),
                McpServerConfig(name="server2", uri="stdio:///path/to/server", timeout_seconds=5.0),
            ],
        )

        status = await contributor.health_check()
        assert status.name == "mcp"
        assert status.status == "down"

    @pytest.mark.asyncio
    async def test_health_check_empty_configs(self) -> None:
        """McpHealthContributor.health_check() should handle empty config list."""
        from stackowl.health.contributors import McpHealthContributor

        probe = AsyncMock(spec=McpLivenessProbe)
        probe.probe_all.return_value = {}

        contributor = McpHealthContributor(probe=probe, configs=[])

        status = await contributor.health_check()
        assert status.name == "mcp"
        assert status.status == "ok"
        assert status.message is not None
        assert "no" in status.message.lower() and "server" in status.message.lower()

    @pytest.mark.asyncio
    async def test_health_contributor_name(self) -> None:
        """McpHealthContributor.contributor_name should return 'mcp'."""
        from stackowl.health.contributors import McpHealthContributor

        probe = AsyncMock(spec=McpLivenessProbe)
        contributor = McpHealthContributor(probe=probe, configs=[])

        assert contributor.contributor_name == "mcp"
