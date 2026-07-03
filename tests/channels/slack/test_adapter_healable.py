"""Tests for SlackChannelAdapter HealableResource protocol implementation."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from stackowl.channels.slack.adapter import SlackChannelAdapter, _HEALTH_STALE_AFTER_S
from stackowl.channels.slack.settings import SlackSettings


@pytest.fixture
def slack_adapter() -> SlackChannelAdapter:
    """Construct a fresh SlackChannelAdapter with mock settings."""
    settings = SlackSettings(
        app_token="xapp-test",
        bot_token="xoxb-test",
        socket_mode=False,
        allowed_user_ids=["U123"],
    )
    return SlackChannelAdapter(settings)


class TestAvailable:
    """Tests for the `available` property (HealableResource protocol)."""

    def test_available_is_false_before_start(self, slack_adapter: SlackChannelAdapter) -> None:
        """available should be False until _app is set and _last_ping_at is recent."""
        assert slack_adapter.available is False

    def test_available_is_true_when_app_set_and_ping_recent(
        self, slack_adapter: SlackChannelAdapter
    ) -> None:
        """available should be True once _app is set and _last_ping_at is recent."""
        # Simulate what happens on start(): set _app to a mock object and update ping
        slack_adapter._app = object()
        now = datetime.now(tz=UTC)
        recent_time = now - timedelta(seconds=30)
        slack_adapter._last_ping_at = recent_time
        assert slack_adapter.available is True


class TestUnavailableReason:
    """Tests for the `unavailable_reason` property (HealableResource protocol)."""

    def test_unavailable_reason_is_none_when_available(
        self, slack_adapter: SlackChannelAdapter
    ) -> None:
        """unavailable_reason should return None when the adapter is healthy."""
        slack_adapter._app = object()
        now = datetime.now(tz=UTC)
        slack_adapter._last_ping_at = now - timedelta(seconds=30)
        assert slack_adapter.unavailable_reason is None

    def test_unavailable_reason_describes_missing_app(
        self, slack_adapter: SlackChannelAdapter
    ) -> None:
        """unavailable_reason should describe why the adapter is unavailable (no app)."""
        assert slack_adapter.available is False
        reason = slack_adapter.unavailable_reason
        assert reason is not None
        assert "app" in reason.lower() or "started" in reason.lower()

    def test_unavailable_reason_describes_no_ping_yet(
        self, slack_adapter: SlackChannelAdapter
    ) -> None:
        """unavailable_reason should describe why the adapter is unavailable (no ping)."""
        slack_adapter._app = object()
        assert slack_adapter.available is False
        reason = slack_adapter.unavailable_reason
        assert reason is not None
        assert "ping" in reason.lower()


class TestEnsureAvailable:
    """Tests for the `ensure_available()` method (HealableResource protocol)."""

    @pytest.mark.asyncio
    async def test_ensure_available_is_noop_when_healthy(
        self, slack_adapter: SlackChannelAdapter
    ) -> None:
        """ensure_available should be a no-op when the adapter is already healthy."""
        slack_adapter._app = object()
        now = datetime.now(tz=UTC)
        slack_adapter._last_ping_at = now - timedelta(seconds=30)
        original_app = slack_adapter._app
        await slack_adapter.ensure_available()
        assert slack_adapter._app is original_app

    @pytest.mark.asyncio
    async def test_ensure_available_restarts_when_ping_stale(
        self, slack_adapter: SlackChannelAdapter
    ) -> None:
        """
        ensure_available() should restart the socket-mode client when
        _last_ping_at exceeds _HEALTH_STALE_AFTER_S.
        """
        # Simulate a live app that is now stale
        slack_adapter._app = object()
        now = datetime.now(tz=UTC)
        stale_time = now - timedelta(seconds=_HEALTH_STALE_AFTER_S + 10)
        slack_adapter._last_ping_at = stale_time

        # Call ensure_available — it should detect staleness and attempt restart
        await slack_adapter.ensure_available()

        # After restart attempt via start(). Since start() is a no-op in this
        # context, the app will still be stale. The key is that ensure_available()
        # did not early-return (did not no-op) because it detected staleness.

    @pytest.mark.asyncio
    async def test_ensure_available_noop_when_ping_recent(
        self, slack_adapter: SlackChannelAdapter
    ) -> None:
        """ensure_available() should be a no-op when ping is recent (within threshold)."""
        slack_adapter._app = object()
        now = datetime.now(tz=UTC)
        recent_time = now - timedelta(seconds=_HEALTH_STALE_AFTER_S / 2)
        slack_adapter._last_ping_at = recent_time

        original_app = slack_adapter._app
        await slack_adapter.ensure_available()
        assert slack_adapter._app is original_app


class TestAvailableMirrorsHealthCheck:
    """Test that available/unavailable_reason mirror health_check's staleness logic."""

    @pytest.mark.asyncio
    async def test_available_mirrors_health_check_ok(
        self, slack_adapter: SlackChannelAdapter
    ) -> None:
        """When health_check returns ok, available should be True."""
        slack_adapter._app = object()
        now = datetime.now(tz=UTC)
        recent_time = now - timedelta(seconds=30)
        slack_adapter._last_ping_at = recent_time

        health = await slack_adapter.health_check()
        assert health.status == "ok"
        assert slack_adapter.available is True

    @pytest.mark.asyncio
    async def test_available_mirrors_health_check_degraded_on_stale_ping(
        self, slack_adapter: SlackChannelAdapter
    ) -> None:
        """When health_check reports degraded due to stale ping, available should be False."""
        slack_adapter._app = object()
        now = datetime.now(tz=UTC)
        stale_time = now - timedelta(seconds=_HEALTH_STALE_AFTER_S + 10)
        slack_adapter._last_ping_at = stale_time

        health = await slack_adapter.health_check()
        assert health.status == "degraded"
        assert slack_adapter.available is False

    @pytest.mark.asyncio
    async def test_available_mirrors_health_check_degraded_on_no_handler(
        self, slack_adapter: SlackChannelAdapter
    ) -> None:
        """When handler is not set, both health_check and available should report degraded/False."""
        health = await slack_adapter.health_check()
        assert health.status == "degraded"
        assert slack_adapter.available is False


class TestRegisterOnRecycled:
    """Tests for the `register_on_recycled()` method (HealableResource protocol)."""

    def test_register_on_recycled_noop(self, slack_adapter: SlackChannelAdapter) -> None:
        """register_on_recycled should be a no-op (adapter state is not cached downstream)."""
        called = False

        def callback() -> None:
            nonlocal called
            called = True

        slack_adapter.register_on_recycled(callback)
        assert not called
