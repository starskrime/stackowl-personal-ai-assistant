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
)
from stackowl.config.settings import Settings
from stackowl.exceptions import ChannelNotFoundError
from stackowl.ipc.connection import FrameConnection
from stackowl.ipc.frames import SendTextFrame


class _FakeConn:
    """Collects frames the proxy emits (stands in for the gateway socket)."""

    def __init__(self) -> None:
        self.sent: list[object] = []

    async def send(self, frame: object) -> None:
        self.sent.append(frame)


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
async def test_proxy_send_ephemeral_does_not_crash_the_canary() -> None:
    """telegram_canary's health-canary send calls send_ephemeral(chat_id, text)
    on whatever adapter it resolves — a proxy missing this method crashed the
    canary every 20m in production (AttributeError). The proxy can't silence
    or self-delete cross-process (no ack frame back), but it MUST still send
    the text and return an int id rather than raising."""
    registry = ChannelRegistry()
    conn = _FakeConn()
    register_socket_channel_proxies(registry, cast(FrameConnection, conn), _settings(telegram="tok"))
    adapter = registry.get("telegram")

    message_id = await adapter.send_ephemeral(99, "canary probe")

    assert isinstance(message_id, int)
    assert len(conn.sent) == 1
    frame = conn.sent[0]
    assert isinstance(frame, SendTextFrame)
    assert frame.text == "canary probe"
    assert frame.target == 99


@pytest.mark.asyncio
async def test_proxy_delete_message_does_not_crash_cleanup() -> None:
    """_best_effort_delete calls delete_message(chat_id, message_id) on whatever
    adapter send_ephemeral resolved to — a proxy missing this method raised
    AttributeError on every ephemeral send in production (caught, but logged
    as an ERROR every tick). The proxy has no ack frame to identify a real
    message to delete, so it must return False rather than raising."""
    registry = ChannelRegistry()
    conn = _FakeConn()
    register_socket_channel_proxies(registry, cast(FrameConnection, conn), _settings(telegram="tok"))
    adapter = registry.get("telegram")

    result = await adapter.delete_message(99, -1)

    assert result is False


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
