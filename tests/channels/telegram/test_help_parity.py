"""Telegram parity (WS-D issue 4): the three-rung /help, ?? dry-run and /find
dispatch through the SAME CommandRegistry and render/chunk cleanly on Telegram.

These drive the REAL registry (load_builtin_commands + a real FindCommand) and
the REAL Telegram outbound path (TelegramMarkdownFormatter → TelegramMessage
Splitter → _send_part) with a mocked bot — sidestepping the TestModeGuard in
send()/send_text() by calling the formatter/splitter/_send_part directly, exactly
as those layers run in production.
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.channels.splitter import TelegramMessageSplitter
from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.commands.find_command import FindCommand
from stackowl.commands.metadata import CommandMeta
from stackowl.commands.registry import CommandRegistry, load_builtin_commands
from stackowl.pipeline.state import PipelineState

pytestmark = pytest.mark.asyncio


def _registry() -> CommandRegistry:
    """Real singleton registry with every builtin + a lexical-only /find."""
    CommandRegistry.reset()
    reg = CommandRegistry.instance()
    load_builtin_commands(reg)
    reg.register(FindCommand(None))  # lexical-only, no embedding model
    return reg


def _state() -> PipelineState:
    return PipelineState(
        trace_id="t-help",
        session_id="42",
        input_text="/help",
        channel="telegram",
        owl_name="system",
        pipeline_step="start",
    )


def _adapter() -> tuple[TelegramChannelAdapter, MagicMock]:
    adapter = TelegramChannelAdapter(
        TelegramSettings(bot_token="x" * 20, allowed_user_ids=frozenset({42}))
    )
    bot = MagicMock()
    bot.send_message = AsyncMock()
    adapter._bot_app = types.SimpleNamespace(bot=bot)
    return adapter, bot


async def _send(adapter: TelegramChannelAdapter, text: str, target: int = 555) -> list[str]:
    """Run the real Telegram outbound path: format → split → send each part."""
    formatted = adapter._formatter.format_response(text)
    parts = adapter._splitter.split(formatted)
    for idx, part in enumerate(parts):
        await adapter._send_part(target, part, idx)
    return parts


def _first_verb_command_with_sub(reg: CommandRegistry) -> tuple[str, str] | None:
    """Find a real ``(command, subcommand)`` pair to exercise rung 3."""
    for cmd in reg.list():
        meta: CommandMeta = cmd.meta
        if meta.grammar == "verb" and meta.subcommands:
            return (cmd.command, meta.subcommands[0].name)
    return None


# --- three-rung /help ------------------------------------------------------


async def test_help_index_renders_and_sends() -> None:
    reg = _registry()
    reply = (await reg.dispatch("help", "", _state())).text
    # Rung-1 index: grouped, with the ▸ "has sub-commands" marker.
    assert "▸" in reply
    adapter, bot = _adapter()
    parts = await _send(adapter, reply)
    assert bot.send_message.await_count == len(parts) >= 1
    # Every part went out as MarkdownV2 (the formatter escaped reserved chars).
    assert all(
        c.kwargs.get("parse_mode") == "MarkdownV2"
        for c in bot.send_message.await_args_list
    )


async def test_help_command_page_renders_and_sends() -> None:
    reg = _registry()
    # /help <command> — use /help itself, which is always registered.
    reply = (await reg.dispatch("help", "help", _state())).text
    assert "help" in reply.lower()
    adapter, bot = _adapter()
    parts = await _send(adapter, reply)
    assert bot.send_message.await_count == len(parts) >= 1


async def test_help_subcommand_page_renders_and_sends() -> None:
    reg = _registry()
    pair = _first_verb_command_with_sub(reg)
    assert pair is not None, "expected at least one verb command with a sub-command"
    cmd, sub = pair
    reply = (await reg.dispatch("help", f"{cmd} {sub}", _state())).text
    assert sub in reply
    adapter, bot = _adapter()
    parts = await _send(adapter, reply)
    assert bot.send_message.await_count == len(parts) >= 1


# --- ?? dry-run + /find ----------------------------------------------------


async def test_dry_run_preview_renders_and_sends() -> None:
    reg = _registry()
    pair = _first_verb_command_with_sub(reg)
    assert pair is not None
    cmd, _sub = pair
    # A trailing `??` previews without running the handler (intercepted in dispatch).
    reply = (await reg.dispatch(cmd, "??", _state())).text
    assert reply.strip()
    adapter, bot = _adapter()
    parts = await _send(adapter, reply)
    assert bot.send_message.await_count == len(parts) >= 1


async def test_find_replies_and_sends() -> None:
    reg = _registry()
    reply = (await reg.dispatch("find", "remember a fact", _state())).text
    assert reply.strip()
    adapter, bot = _adapter()
    parts = await _send(adapter, reply)
    assert bot.send_message.await_count == len(parts) >= 1


# --- chunking: long /help splits into multiple Telegram messages ----------


async def test_help_chunks_over_the_limit() -> None:
    reg = _registry()
    reply = (await reg.dispatch("help", "", _state())).text

    adapter, bot = _adapter()

    class _TinySplitter(TelegramMessageSplitter):
        @property
        def char_limit(self) -> int:
            return 200

    adapter._splitter = _TinySplitter()
    parts = await _send(adapter, reply)
    # The index is well over 200 chars → it must split into multiple messages,
    # and EVERY part must be delivered (none dropped).
    assert len(parts) > 1
    assert bot.send_message.await_count == len(parts)
    # Fence-safe split never severs a code fence (odd ``` count) in any part.
    assert all(part.count("```") % 2 == 0 for part in parts)
