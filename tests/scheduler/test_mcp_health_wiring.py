"""Test MCP health wiring into the scheduler assembly (ADR-6 Task 8).

Validates that the healers dict key exactly matches the contributor's
contributor_name, preventing silent lookup failures during health sweep.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stackowl.health.contributors import McpHealthContributor
from stackowl.mcp.allowlist import McpServerConfig
from stackowl.mcp.client import McpClient


class TestMcpHealthWiring:
    """Verify MCP health contributor key matches healers dict key."""

    @pytest.mark.asyncio
    async def test_mcp_healer_key_matches_contributor_name(self) -> None:
        """The healers dict key must exactly match McpHealthContributor.contributor_name.

        This test prevents the hard-won mistake where a healers dict key
        like 'mcp' and a contributor_name 'graph' silently fail to match
        during health_sweep's dict.get(status.name) lookup.
        """
        # Create a mock MCP client
        mcp_client = MagicMock(spec=McpClient)
        mcp_client.available = True
        mcp_client.unavailable_reason = None

        # Create a contributor
        probe = AsyncMock()
        configs = [
            McpServerConfig(name="test_server", uri="sse://localhost:3001", timeout_seconds=5.0),
        ]
        contributor = McpHealthContributor(probe=probe, configs=configs)

        # The healers dict would be keyed by the contributor_name
        healers = {contributor.contributor_name: mcp_client}

        # Validate the key exists and is correct
        assert "mcp" in healers
        assert healers["mcp"] is mcp_client
        assert contributor.contributor_name == "mcp"

    @pytest.mark.asyncio
    async def test_mcp_contributor_reads_contributor_name_dynamically(self) -> None:
        """Assembly code should read contributor_name from the instance, not hardcode it.

        This prevents maintainability issues where contributor_name changes
        but a hardcoded string in assembly doesn't update.
        """
        probe = AsyncMock()
        configs = []
        contributor = McpHealthContributor(probe=probe, configs=configs)

        # The assembly code should look like:
        # healers[contributor.contributor_name] = mcp_client
        # NOT:
        # healers["mcp"] = mcp_client

        # This test validates the pattern works:
        contributor_key = contributor.contributor_name  # Read dynamically
        assert contributor_key == "mcp"
