"""Tests for SlackChannelAdapter HealableResource protocol implementation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stackowl.channels.slack.adapter import _HEALTH_STALE_AFTER_S, SlackChannelAdapter
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
    async def test_ensure_available_invokes_reconnector_when_stale(
        self, slack_adapter: SlackChannelAdapter
    ) -> None:
        """
        ensure_available() must invoke the injected reconnector callback
        exactly once when _last_ping_at exceeds _HEALTH_STALE_AFTER_S — this
        IS the real recovery action (start() performs no network I/O by
        design; see its docstring). Regression test for the bug where
        ensure_available() called start() and did nothing observable.
        """
        slack_adapter._app = object()
        now = datetime.now(tz=UTC)
        stale_time = now - timedelta(seconds=_HEALTH_STALE_AFTER_S + 10)
        slack_adapter._last_ping_at = stale_time

        calls = 0

        async def fake_reconnector() -> None:
            nonlocal calls
            calls += 1

        slack_adapter.set_reconnector(fake_reconnector)
        await slack_adapter.ensure_available()

        assert calls == 1

    @pytest.mark.asyncio
    async def test_ensure_available_does_not_invoke_reconnector_when_healthy(
        self, slack_adapter: SlackChannelAdapter
    ) -> None:
        """ensure_available() must NOT call the reconnector when already
        healthy — the double-heal guard (`if self.available: return`)."""
        slack_adapter._app = object()
        now = datetime.now(tz=UTC)
        recent_time = now - timedelta(seconds=_HEALTH_STALE_AFTER_S / 2)
        slack_adapter._last_ping_at = recent_time

        calls = 0

        async def fake_reconnector() -> None:
            nonlocal calls
            calls += 1

        slack_adapter.set_reconnector(fake_reconnector)
        await slack_adapter.ensure_available()

        assert calls == 0

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

    @pytest.mark.asyncio
    async def test_ensure_available_stale_without_reconnector_does_not_crash(
        self, slack_adapter: SlackChannelAdapter
    ) -> None:
        """No reconnector injected (adapter used outside the full orchestrator
        boot path, e.g. isolated tests) → logged no-op, never a crash."""
        slack_adapter._app = object()
        now = datetime.now(tz=UTC)
        stale_time = now - timedelta(seconds=_HEALTH_STALE_AFTER_S + 10)
        slack_adapter._last_ping_at = stale_time

        # Must not raise.
        await slack_adapter.ensure_available()


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
