"""CHAN-4 (F013) — Discord send_file / download_media round-trip.

Discord uploads a file via ``channel.send(file=discord.File(...))`` to the
resolved channel (same per-session target threading as send_text) and reads an
inbound attachment's bytes via ``attachment.read()``. A file send must never
crash the turn (self-healing); an explicit-but-unresolvable target on send_file
fails loud (no silent drop), best-effort is a logged no-op.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from stackowl.channels.discord.adapter import DiscordChannelAdapter
from stackowl.channels.discord.settings import DiscordSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import DeliveryError


def _adapter() -> DiscordChannelAdapter:
    return DiscordChannelAdapter(
        DiscordSettings(bot_token="x" * 8, allowed_user_ids=[42])
    )


def _channel(channel_id: int) -> tuple[Any, AsyncMock]:
    send = AsyncMock()
    return types.SimpleNamespace(id=channel_id, send=send), send


@pytest.mark.asyncio
async def test_send_file_uploads_to_resolved_channel(tmp_path: Path) -> None:
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        ch, send = _channel(77)
        adapter._channels[77] = ch
        f = tmp_path / "report.txt"
        f.write_text("hello report")
        await adapter.send_file(str(f), caption="here", channel_id=77)
        send.assert_awaited()
        # The upload passes a discord.File via the file= kwarg.
        kwargs = send.await_args.kwargs
        assert "file" in kwargs
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_send_file_explicit_unresolvable_raises(tmp_path: Path) -> None:
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        f = tmp_path / "x.txt"
        f.write_text("x")
        with pytest.raises(DeliveryError):
            await adapter.send_file(str(f), channel_id=999)
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_send_file_best_effort_noop_no_target(tmp_path: Path) -> None:
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        f = tmp_path / "x.txt"
        f.write_text("x")
        assert adapter._last_channel_id is None
        # No explicit target + no _last_channel_id → logged no-op, never raises.
        await adapter.send_file(str(f))
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_download_media_reads_cached_attachment() -> None:
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        attachment = types.SimpleNamespace(
            id=555, read=AsyncMock(return_value=b"file-bytes")
        )
        # An inbound attachment is cached by its string id for later download.
        adapter._attachments["555"] = attachment
        data = await adapter.download_media("555")
        assert data == b"file-bytes"
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_download_media_unknown_id_raises() -> None:
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        with pytest.raises(RuntimeError):
            await adapter.download_media("nope")
    finally:
        TestModeGuard.deactivate()
