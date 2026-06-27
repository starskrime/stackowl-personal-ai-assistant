"""Tests for ProactiveDeliverer (E7-S0) — outbound transport bridge."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from stackowl.channels.registry import ChannelRegistry
from stackowl.config.notification_settings import NotificationSettings
from stackowl.config.settings import Settings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.notifications.deliverer import ProactiveDeliverer, clamp_agent_urgency
from stackowl.notifications.router import (
    DeliveryStatus,
    Notification,
    NotificationRouter,
)


def _settings(default_channel: str = "cli") -> Settings:
    ns = SimpleNamespace(
        notifications=NotificationSettings(default_channel=default_channel)
    )
    return cast(Settings, ns)


class _RecordingAdapter:
    """Minimal channel adapter that records send_text calls."""

    def __init__(self, name: str = "cli") -> None:
        self._name = name
        self.sent: list[str] = []
        self.fail_times = 0

    @property
    def channel_name(self) -> str:
        return self._name

    async def send_text(self, text: str) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("transient send failure")
        self.sent.append(text)


class _StubRouter:
    """Router stub that returns a preset decision without DB/test-mode checks."""

    def __init__(self, decision: DeliveryStatus) -> None:
        self._decision = decision
        self.calls: list[Notification] = []

    async def deliver(self, notification: Notification) -> DeliveryStatus:
        self.calls.append(notification)
        return self._decision


@pytest.fixture(autouse=True)
def _clean_registry():  # type: ignore[no-untyped-def]
    ChannelRegistry.instance().reset()
    yield
    ChannelRegistry.instance().reset()


def _deliverer(decision: DeliveryStatus, *, default_channel: str = "cli") -> tuple[
    ProactiveDeliverer, _StubRouter, ChannelRegistry
]:
    router = _StubRouter(decision)
    registry = ChannelRegistry.instance()
    dlv = ProactiveDeliverer(
        router=cast(NotificationRouter, router),
        registry=registry,
        settings=_settings(default_channel),
    )
    return dlv, router, registry


def _note(channel: str | None = "cli", urgency: str = "normal") -> Notification:
    return Notification(
        message="hello body",
        urgency=urgency,  # type: ignore[arg-type]
        category="test",
        channel_name=channel,
    )


async def test_delivered_sends_once_with_body_and_channel() -> None:
    dlv, _router, registry = _deliverer("delivered")
    adapter = _RecordingAdapter("cli")
    registry.register(adapter)

    status = await dlv.deliver(_note("cli"))

    assert status == "delivered"
    assert adapter.sent == ["hello body"]


async def test_batched_does_not_send() -> None:
    dlv, _router, registry = _deliverer("batched")
    adapter = _RecordingAdapter("cli")
    registry.register(adapter)

    status = await dlv.deliver(_note("cli"))

    assert status == "batched"
    assert adapter.sent == []


async def test_suppressed_does_not_send() -> None:
    dlv, _router, registry = _deliverer("suppressed")
    adapter = _RecordingAdapter("cli")
    registry.register(adapter)

    status = await dlv.deliver(_note("cli"))

    assert status == "suppressed"
    assert adapter.sent == []


async def test_unknown_channel_returns_failed_no_raise() -> None:
    dlv, _router, _registry = _deliverer("delivered")
    # No adapter registered for "cli" → registry.get raises ChannelNotFoundError.
    status = await dlv.deliver(_note("cli"))
    assert status == "failed"


async def test_send_raises_retry_once_then_failed() -> None:
    dlv, _router, registry = _deliverer("delivered")
    adapter = _RecordingAdapter("cli")
    adapter.fail_times = 5  # always fails → fails after retry-once
    registry.register(adapter)

    status = await dlv.deliver(_note("cli"))

    assert status == "failed"
    assert adapter.sent == []


async def test_send_transient_then_success_via_retry() -> None:
    dlv, _router, registry = _deliverer("delivered")
    adapter = _RecordingAdapter("cli")
    adapter.fail_times = 1  # first attempt fails, retry succeeds
    registry.register(adapter)

    status = await dlv.deliver(_note("cli"))

    assert status == "delivered"
    assert adapter.sent == ["hello body"]


async def test_default_channel_used_when_none() -> None:
    dlv, _router, registry = _deliverer("delivered", default_channel="cli")
    adapter = _RecordingAdapter("cli")
    registry.register(adapter)

    status = await dlv.deliver(_note(channel=None))

    assert status == "delivered"
    assert adapter.sent == ["hello body"]


async def test_transport_helper_sends_without_redeciding() -> None:
    dlv, router, registry = _deliverer("suppressed")  # decision irrelevant here
    adapter = _RecordingAdapter("cli")
    registry.register(adapter)

    status = await dlv.transport("cli", "queued body")

    assert status == "delivered"
    assert adapter.sent == ["queued body"]
    assert router.calls == []  # router.deliver NOT called by transport()


# --------------------------------------------------------------- urgency clamp


def test_clamp_agent_normal_passes() -> None:
    assert clamp_agent_urgency("normal") == "normal"


def test_clamp_agent_low_passes() -> None:
    assert clamp_agent_urgency("low") == "low"


def test_clamp_agent_critical_downgrades_to_normal() -> None:
    assert clamp_agent_urgency("critical") == "normal"


def test_clamp_unknown_downgrades_to_normal() -> None:
    assert clamp_agent_urgency("bogus") == "normal"


async def test_system_critical_preserved_without_clamp(tmp_db: DbPool) -> None:
    """System callers don't use the clamp → router honors a critical send.

    Critical always routes to ``delivered`` regardless of focus/quiet.
    """
    TestModeGuard.deactivate()
    router = NotificationRouter(
        db=tmp_db,
        settings=_settings(),
        clock=lambda: datetime(2026, 5, 30, tzinfo=UTC),
    )
    note = Notification(message="alarm", urgency="critical", category="sys")
    status = await router.deliver(note)
    assert status == "delivered"


# --- ADR-2: reroute a FAILED transport to an opt-in fallback channel -----------


def _deliverer_fb(
    decision: DeliveryStatus, *, default_channel: str = "cli", fallback_channel: str = ""
) -> tuple[ProactiveDeliverer, ChannelRegistry]:
    router = _StubRouter(decision)
    registry = ChannelRegistry.instance()
    ns = SimpleNamespace(
        notifications=NotificationSettings(
            default_channel=default_channel, fallback_channel=fallback_channel
        )
    )
    dlv = ProactiveDeliverer(
        router=cast(NotificationRouter, router),
        registry=registry,
        settings=cast(Settings, ns),
    )
    return dlv, registry


async def test_failed_transport_reroutes_to_fallback() -> None:
    dlv, registry = _deliverer_fb("delivered", fallback_channel="telegram")
    primary = _RecordingAdapter("cli")
    primary.fail_times = 99  # both in-channel attempts fail
    fallback = _RecordingAdapter("telegram")
    registry.register(primary)
    registry.register(fallback)

    status = await dlv.deliver(_note("cli"))

    assert status == "delivered"  # rerouted, not surrendered
    assert fallback.sent == ["hello body"]  # the fallback channel got the message
    assert primary.sent == []


async def test_no_fallback_is_byte_identical() -> None:
    dlv, registry = _deliverer_fb("delivered", fallback_channel="")  # opt-in OFF
    primary = _RecordingAdapter("cli")
    primary.fail_times = 99
    registry.register(primary)

    status = await dlv.deliver(_note("cli"))

    assert status == "failed"  # no reroute — today's behavior


async def test_reroute_skipped_when_fallback_equals_failed_channel() -> None:
    dlv, registry = _deliverer_fb("delivered", fallback_channel="cli")
    primary = _RecordingAdapter("cli")
    primary.fail_times = 99
    registry.register(primary)

    status = await dlv.deliver(_note("cli"))

    assert status == "failed"  # never reroute to the same channel that just failed


async def test_reroute_that_also_fails_returns_failed() -> None:
    dlv, registry = _deliverer_fb("delivered", fallback_channel="telegram")
    primary = _RecordingAdapter("cli")
    primary.fail_times = 99
    fallback = _RecordingAdapter("telegram")
    fallback.fail_times = 99  # fallback also down
    registry.register(primary)
    registry.register(fallback)

    status = await dlv.deliver(_note("cli"))

    assert status == "failed"  # ladder exhausted → honest failure
