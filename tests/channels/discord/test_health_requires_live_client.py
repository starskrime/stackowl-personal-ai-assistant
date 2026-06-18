"""F004-part1 — Discord/WhatsApp health gates ``ok`` on LIVENESS, not heartbeat.

Discord is dead code until F004-part2 wires startup. Reporting ``ok`` purely
from a fresh heartbeat would lie about send capability (there is no live client
to send through). The fix: ``health_check`` requires the real transport to be
live (Discord: ``_client is not None``; WhatsApp: the poll loop started) before
it may report ``ok``.
"""

from __future__ import annotations

import time as _time

import pytest

from stackowl.channels.discord.adapter import DiscordChannelAdapter
from stackowl.channels.discord.settings import DiscordSettings
from stackowl.channels.whatsapp.adapter import WhatsAppChannelAdapter
from stackowl.channels.whatsapp.settings import WhatsAppSettings


@pytest.mark.asyncio
async def test_discord_health_not_ok_without_live_client() -> None:
    adapter = DiscordChannelAdapter(
        DiscordSettings(bot_token="x" * 8, allowed_user_ids=[1])
    )
    # Fresh heartbeat but NO live client → must NOT report ok.
    adapter._last_heartbeat_at = _time.monotonic()
    assert adapter._client is None
    status = await adapter.health_check()
    assert status.status != "ok"


@pytest.mark.asyncio
async def test_discord_health_ok_with_live_client_and_heartbeat() -> None:
    adapter = DiscordChannelAdapter(
        DiscordSettings(bot_token="x" * 8, allowed_user_ids=[1])
    )
    adapter._client = object()  # type: ignore[assignment]  # stand-in for a live client
    adapter._last_heartbeat_at = _time.monotonic()
    status = await adapter.health_check()
    assert status.status == "ok"


@pytest.mark.asyncio
async def test_whatsapp_health_not_ok_without_poll_loop() -> None:
    adapter = WhatsAppChannelAdapter(
        WhatsAppSettings(
            allowed_phone_numbers=frozenset(["1"]),
            session_dir="/tmp/test_wa_health",
        ),
        data_dir="/tmp/test_data",
    )
    # A poll timestamp could be set, but no poll task is running → not ok.
    adapter._last_poll_at = _time.monotonic()
    assert adapter._poll_task is None
    status = await adapter.health_check()
    assert status.status != "ok"


def test_discord_settings_enabled_defaults_false() -> None:
    assert DiscordSettings().enabled is False


def test_whatsapp_settings_enabled_defaults_false() -> None:
    assert WhatsAppSettings().enabled is False
