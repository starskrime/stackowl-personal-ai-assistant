"""F-65 — Telegram send_file delivery-honesty contract.

``send_file`` must mirror ``send_text``'s explicit/best-effort target handling:
an EXPLICIT on-turn target that cannot reach a live bot fails loud
(``DeliveryError`` the ProactiveDeliverer maps to ``failed``) rather than a
silent ``return`` that drops the file while the ledger records a clean send. A
best-effort send with no explicit target and no ``_last_chat_id`` stays a logged
no-op (never raises — preserves the proactive deliverer's never-raises contract).
The upload-exception path (a resolved chat whose Bot API call fails) propagates
unchanged to the deliverer.
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import DeliveryError


def _settings() -> TelegramSettings:
    return TelegramSettings(bot_token="test_token_x" * 3, allowed_user_ids=frozenset({42}))


def _adapter_with_bot() -> tuple[TelegramChannelAdapter, MagicMock]:
    adapter = TelegramChannelAdapter(_settings())
    bot = MagicMock()
    bot.send_document = AsyncMock()
    bot.send_photo = AsyncMock()
    bot.send_video = AsyncMock()
    adapter._bot_app = types.SimpleNamespace(bot=bot)
    return adapter, bot


@pytest.mark.asyncio
async def test_send_file_uploads_to_explicit_chat(tmp_path: Path) -> None:
    TestModeGuard.deactivate()
    try:
        adapter, bot = _adapter_with_bot()
        f = tmp_path / "report.txt"
        f.write_text("hello report")
        await adapter.send_file(str(f), caption="here", chat_id=77)
        bot.send_document.assert_awaited_once()
        assert bot.send_document.await_args.kwargs["chat_id"] == 77
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_send_file_explicit_target_no_bot_raises(tmp_path: Path) -> None:
    """An explicit on-turn chat_id with no live bot must fail loud (no silent drop)."""
    TestModeGuard.deactivate()
    try:
        adapter = TelegramChannelAdapter(_settings())
        assert adapter._bot_app is None
        f = tmp_path / "x.txt"
        f.write_text("x")
        with pytest.raises(DeliveryError) as ei:
            await adapter.send_file(str(f), chat_id=999)
        assert ei.value.channel == "telegram"
        assert ei.value.reason == "no_channel"
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_send_file_best_effort_no_target_is_noop(tmp_path: Path) -> None:
    """No explicit chat_id + no _last_chat_id → logged no-op, never raises."""
    TestModeGuard.deactivate()
    try:
        adapter, bot = _adapter_with_bot()
        assert adapter._last_chat_id is None
        f = tmp_path / "x.txt"
        f.write_text("x")
        await adapter.send_file(str(f))  # best-effort, no target
        bot.send_document.assert_not_awaited()
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_send_file_upload_error_propagates(tmp_path: Path) -> None:
    """A resolved chat whose Bot API upload fails propagates to the deliverer."""
    TestModeGuard.deactivate()
    try:
        adapter, bot = _adapter_with_bot()
        bot.send_document = AsyncMock(side_effect=RuntimeError("upload boom"))
        adapter._bot_app = types.SimpleNamespace(bot=bot)
        f = tmp_path / "x.txt"
        f.write_text("x")
        with pytest.raises(RuntimeError):
            await adapter.send_file(str(f), chat_id=77)
    finally:
        TestModeGuard.deactivate()
