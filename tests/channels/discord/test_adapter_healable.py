"""Tests for DiscordChannelAdapter HealableResource protocol implementation.

Tests the ADR-6 self-healing protocol: available/unavailable_reason/ensure_available/register_on_recycled.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from stackowl.channels.discord.adapter import DiscordChannelAdapter
from stackowl.channels.discord.settings import DiscordSettings
from stackowl.config.test_mode import TestModeGuard


@pytest.fixture
def settings():
    """Minimal Discord settings for testing."""
    return DiscordSettings(
        bot_token="test_token",
        allowed_user_ids=[123456],
        enabled=True,
    )


@pytest.fixture
def adapter(settings):
    """Create a Discord adapter with test settings."""
    TestModeGuard.activate()
    adapter = DiscordChannelAdapter(settings)
    yield adapter
    TestModeGuard.deactivate()


class TestAvailableProperty:
    """Test the `available` property."""

    def test_available_true_when_client_exists(self, adapter):
        """available should return True when _client is set."""
        adapter._client = MagicMock()
        assert adapter.available is True

    def test_available_false_when_client_none(self, adapter):
        """available should return False when _client is None."""
        adapter._client = None
        assert adapter.available is False


class TestUnavailableReason:
    """Test the `unavailable_reason` property."""

    def test_unavailable_reason_none_when_available(self, adapter):
        """unavailable_reason should return None when available."""
        adapter._client = MagicMock()
        assert adapter.unavailable_reason is None

    def test_unavailable_reason_when_client_none(self, adapter):
        """unavailable_reason should return a message when client is None."""
        adapter._client = None
        reason = adapter.unavailable_reason
        assert reason is not None
        assert "no live client" in reason or "not" in reason.lower()


class TestEnsureAvailable:
    """Test the `ensure_available()` method."""

    @pytest.mark.asyncio
    async def test_ensure_available_calls_start_when_client_none(self, adapter):
        """ensure_available() should call start() when _client is None."""
        adapter._client = None

        # Mock the start method to track if it's called
        adapter.start = AsyncMock()

        await adapter.ensure_available()

        # start() should have been called
        adapter.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_available_noop_when_client_exists(self, adapter):
        """ensure_available() should be a no-op when _client is not None."""
        adapter._client = MagicMock()

        # Mock the start method to track if it's called
        adapter.start = AsyncMock()

        await adapter.ensure_available()

        # start() should NOT have been called
        adapter.start.assert_not_called()


class TestRegisterOnRecycled:
    """Test the `register_on_recycled()` method."""

    def test_register_on_recycled_is_noop(self, adapter):
        """register_on_recycled() should be a no-op and not raise."""
        callback = lambda: None
        # Should not raise
        adapter.register_on_recycled(callback)


class TestContributorName:
    """Test that contributor_name matches HealableResource protocol."""

    def test_contributor_name_is_discord(self, adapter):
        """contributor_name should be 'discord' for wiring into healers dict."""
        assert adapter.contributor_name == "discord"
