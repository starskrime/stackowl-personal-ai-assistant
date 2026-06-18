"""Integration guards for two wired features in TelegramChannelAdapter.

W1: adapter.start() must call app.bot.set_my_commands with the CommandRegistry contents.
W2: adapter._handle_update must apply strip_command_bot_suffix before building IngressMessage.

Both tests FAIL if the corresponding wiring line is removed from adapter.py.
"""

from __future__ import annotations

import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.config.test_mode import TestModeGuard
from stackowl.pipeline.state import PipelineState


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _settings() -> TelegramSettings:
    return TelegramSettings(
        bot_token="test_token_x" * 3,
        allowed_user_ids=frozenset({42}),
    )


def _make_update(text: str, user_id: int, chat_id: int = 100) -> Any:
    """Build a duck-typed Update-like object for _handle_update tests."""
    user = types.SimpleNamespace(id=user_id)
    message = types.SimpleNamespace(text=text)
    chat = types.SimpleNamespace(id=chat_id)
    return types.SimpleNamespace(
        effective_message=message,
        effective_user=user,
        effective_chat=chat,
    )


# ---------------------------------------------------------------------------
# Stub SlashCommand for W1
# ---------------------------------------------------------------------------


class _StubCommand(SlashCommand):
    @property
    def command(self) -> str:
        return "help"

    @property
    def description(self) -> str:
        return "Show help"

    async def handle(self, args: str, state: PipelineState) -> str:
        return "ok"


# ---------------------------------------------------------------------------
# W1 — start() must register the slash-command menu via set_my_commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_registers_slash_command_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    """start() must call app.bot.set_my_commands with the CommandRegistry contents.

    This test FAILS if the `await register_commands(app.bot, ...)` line is
    removed from TelegramChannelAdapter.start().
    """
    # 1. Neutralize TestModeGuard so start() is not blocked.
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", staticmethod(lambda op: None))

    # 2. Populate CommandRegistry with at least one command.
    CommandRegistry.reset()
    CommandRegistry.instance().register(_StubCommand())

    # 3. Build a fake bot that records set_my_commands calls.
    fake_bot = MagicMock()
    set_my_commands_calls: list[Any] = []

    async def _record_set_my_commands(cmds: Any) -> None:
        set_my_commands_calls.append(cmds)

    fake_bot.set_my_commands = _record_set_my_commands

    # 4. Build a fake app that exposes the fake bot.
    fake_app = MagicMock()
    fake_app.bot = fake_bot
    fake_app.add_handler = MagicMock()

    # 5. Mock start_bot to return our fake (app, bot_id, bot_username) triple.
    async def _fake_start_bot(token: str, webhook_url: Any, webhook_secret: Any) -> tuple[Any, int, str]:
        return fake_app, 12345, "TestBot"

    monkeypatch.setattr(
        "stackowl.channels.telegram.adapter.start_bot",
        _fake_start_bot,
    )

    # 6. Mock register_with_registry to avoid ChannelRegistry side effects.
    monkeypatch.setattr(
        TelegramChannelAdapter,
        "register_with_registry",
        lambda self: None,
    )

    # 7. Run start().
    adapter = TelegramChannelAdapter(_settings())
    await adapter.start()

    # 8. Assert set_my_commands was called at least once with a non-empty list.
    assert set_my_commands_calls, (
        "set_my_commands was never called — the register_commands wiring in start() is missing"
    )
    bot_commands = set_my_commands_calls[0]
    assert bot_commands, "set_my_commands was called with an empty list"

    # 9. Assert the command names match the registry.
    registered_names = {cmd.command for cmd in CommandRegistry.instance().list()}
    pushed_names = {bc.command for bc in bot_commands}
    assert pushed_names == registered_names, (
        f"pushed commands {pushed_names!r} don't match registry {registered_names!r}"
    )

    # Cleanup singleton so other tests are not polluted.
    CommandRegistry.reset()


# ---------------------------------------------------------------------------
# W2 — _handle_update must strip /cmd@BotName suffix before building IngressMessage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_update_strips_command_bot_suffix() -> None:
    """_handle_update must call strip_command_bot_suffix so /help@BotName → /help.

    This test FAILS if the `stripped = strip_command_bot_suffix(...)` line is
    removed from TelegramChannelAdapter._handle_update().
    """
    adapter = TelegramChannelAdapter(_settings())
    # Set the bot username the adapter would normally learn from start_bot.
    adapter._bot_username = "TestBot"

    # Simulate a group message where Telegram appends @BotName to the command.
    update = _make_update("/help@TestBot", user_id=42, chat_id=200)
    ctx: Any = types.SimpleNamespace()
    await adapter._handle_update(update, ctx)

    assert adapter._queue.qsize() == 1, "No IngressMessage was enqueued"
    msg = await adapter._queue.get()
    assert msg.text == "/help", (
        f"Expected '/help' but got {msg.text!r} — "
        "strip_command_bot_suffix wiring in _handle_update may be missing"
    )
