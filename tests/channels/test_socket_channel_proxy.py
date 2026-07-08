"""Cross-process proactive delivery — the core pre-registers socket proxies.

Root-cause guard: in split mode the core owns no real channel adapter, so a
proactive/scheduled send resolved nothing in its ChannelRegistry and raised
``ChannelNotFoundError``. ``register_socket_channel_proxies`` registers a
:class:`SocketChannelAdapter` proxy per gateway-configured channel so the deliverer
resolves it and emits a ``SendTextFrame`` across the socket to the gateway's real
adapter. Mono/gateway roles never call this (additive, core-only).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from stackowl.channels.registry import ChannelRegistry
from stackowl.channels.socket_adapter import (
    configured_gateway_channels,
    register_socket_channel_proxies,
    resolve_ephemeral_sent,
)
from stackowl.config.settings import Settings
from stackowl.exceptions import ChannelNotFoundError
from stackowl.ipc.connection import FrameConnection
from stackowl.ipc.frames import DeleteMessageFrame, SendEphemeralFrame, SendTextFrame


class _FakeConn:
    """Collects frames the proxy emits (stands in for the gateway socket).

    Auto-acks a ``SendEphemeralFrame`` with ``next_message_id`` — simulating the
    gateway's instant ``EphemeralSentFrame`` reply — so tests can await
    ``send_ephemeral`` without a real socket round-trip.
    """

    def __init__(self) -> None:
        self.sent: list[object] = []
        self.next_message_id = 4242

    async def send(self, frame: object) -> None:
        self.sent.append(frame)
        if isinstance(frame, SendEphemeralFrame):
            resolve_ephemeral_sent(frame.request_id, self.next_message_id)


def _settings(**k: object) -> Settings:
    def cfg(**kw: object) -> SimpleNamespace:
        return SimpleNamespace(**kw)

    ns = SimpleNamespace(
        telegram_channel=cfg(bot_token=k.get("telegram")),
        slack_channel=cfg(bot_token=k.get("slack_bot"), app_token=k.get("slack_app")),
        discord_channel=cfg(
            enabled=k.get("discord_enabled", False), bot_token=k.get("discord_token")
        ),
        whatsapp_channel=cfg(enabled=k.get("whatsapp_enabled", False)),
    )
    return cast(Settings, ns)


def test_configured_channels_reads_all_gates() -> None:
    s = _settings(
        telegram="tok",
        slack_bot="b",
        slack_app="a",
        discord_enabled=True,
        discord_token="d",
        whatsapp_enabled=True,
    )
    assert configured_gateway_channels(s) == ["telegram", "slack", "discord", "whatsapp"]


def test_configured_channels_empty_when_unconfigured() -> None:
    assert configured_gateway_channels(_settings()) == []


def test_slack_requires_both_tokens() -> None:
    assert configured_gateway_channels(_settings(slack_bot="b")) == []
    assert configured_gateway_channels(_settings(slack_bot="b", slack_app="a")) == ["slack"]


@pytest.mark.asyncio
async def test_proxy_resolves_and_emits_frame_across_socket() -> None:
    registry = ChannelRegistry()
    conn = _FakeConn()
    # Before the fix this channel is absent -> ChannelNotFoundError.
    with pytest.raises(ChannelNotFoundError):
        registry.get("telegram")

    names = register_socket_channel_proxies(
        registry, cast(FrameConnection, conn), _settings(telegram="tok")
    )
    assert names == ["telegram"]

    adapter = registry.get("telegram")  # now resolves
    await adapter.send_text("proactive ping", chat_id=99)

    assert len(conn.sent) == 1
    frame = conn.sent[0]
    assert isinstance(frame, SendTextFrame)
    assert frame.channel == "telegram"
    assert frame.text == "proactive ping"
    assert frame.target == 99


@pytest.mark.asyncio
async def test_proxy_send_ephemeral_round_trips_real_message_id() -> None:
    """telegram_canary's health-canary send calls send_ephemeral(chat_id, text)
    on whatever adapter it resolves. The proxy round-trips a SendEphemeralFrame
    to the gateway and awaits the correlated EphemeralSentFrame reply carrying
    the gateway's real Telegram message_id, so a later delete_message can
    actually remove the probe (fixes the canary staying visible forever)."""
    registry = ChannelRegistry()
    conn = _FakeConn()
    conn.next_message_id = 777
    register_socket_channel_proxies(registry, cast(FrameConnection, conn), _settings(telegram="tok"))
    adapter = registry.get("telegram")

    message_id = await adapter.send_ephemeral(99, "canary probe")

    assert message_id == 777
    assert len(conn.sent) == 1
    frame = conn.sent[0]
    assert isinstance(frame, SendEphemeralFrame)
    assert frame.text == "canary probe"
    assert frame.target == 99


@pytest.mark.asyncio
async def test_proxy_send_ephemeral_falls_back_to_sentinel_on_no_ack() -> None:
    """No EphemeralSentFrame ever arrives (e.g. gateway down) -> timeout, not a crash."""
    registry = ChannelRegistry()

    class _SilentConn(_FakeConn):
        async def send(self, frame: object) -> None:  # never acks
            self.sent.append(frame)

    conn = _SilentConn()
    register_socket_channel_proxies(registry, cast(FrameConnection, conn), _settings(telegram="tok"))
    adapter = registry.get("telegram")

    from stackowl.channels import socket_adapter as socket_adapter_module

    original_timeout = socket_adapter_module._EPHEMERAL_ACK_TIMEOUT_SECONDS
    socket_adapter_module._EPHEMERAL_ACK_TIMEOUT_SECONDS = 0.05
    try:
        message_id = await adapter.send_ephemeral(99, "canary probe")
    finally:
        socket_adapter_module._EPHEMERAL_ACK_TIMEOUT_SECONDS = original_timeout

    assert message_id == -1


@pytest.mark.asyncio
async def test_proxy_delete_message_sends_delete_frame_for_real_id() -> None:
    """delete_message with a real (gateway-acked) message_id fires a
    DeleteMessageFrame across the socket so the gateway can actually delete it."""
    registry = ChannelRegistry()
    conn = _FakeConn()
    register_socket_channel_proxies(registry, cast(FrameConnection, conn), _settings(telegram="tok"))
    adapter = registry.get("telegram")

    result = await adapter.delete_message(99, 777)

    assert result is True
    assert len(conn.sent) == 1
    frame = conn.sent[0]
    assert isinstance(frame, DeleteMessageFrame)
    assert frame.channel == "telegram"
    assert frame.target == 99
    assert frame.message_id == 777


@pytest.mark.asyncio
async def test_proxy_delete_message_noop_for_sentinel_id() -> None:
    """message_id < 0 means send_ephemeral never got a real id — nothing to delete."""
    registry = ChannelRegistry()
    conn = _FakeConn()
    register_socket_channel_proxies(registry, cast(FrameConnection, conn), _settings(telegram="tok"))
    adapter = registry.get("telegram")

    result = await adapter.delete_message(99, -1)

    assert result is False
    assert len(conn.sent) == 0


@pytest.mark.asyncio
async def test_registration_is_idempotent() -> None:
    registry = ChannelRegistry()
    conn = _FakeConn()
    s = _settings(telegram="tok")
    assert register_socket_channel_proxies(registry, cast(FrameConnection, conn), s) == [
        "telegram"
    ]
    # A second pass (or the reactive inbound path having won) registers nothing new.
    assert register_socket_channel_proxies(registry, cast(FrameConnection, conn), s) == []
