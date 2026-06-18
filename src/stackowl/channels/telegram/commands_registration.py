"""Register StackOwl slash commands with Telegram so the client shows the menu.

RC-D fix: without set_my_commands, Telegram never learns the bot's command list,
so "/" shows no autocomplete. We translate the shared CommandRegistry into PTB
BotCommand objects (respecting Telegram's name/description constraints) and push
them on bot start.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover
    from telegram import BotCommand

_NAME_RE = re.compile(r"[^a-z0-9_]")
_MAX_NAME = 32
_MAX_DESC = 256


def build_bot_commands(commands: list[Any]) -> list[BotCommand]:
    """Translate SlashCommand objects to Telegram BotCommand, enforcing TG limits."""
    from telegram import BotCommand

    out: list[BotCommand] = []
    seen: set[str] = set()
    for c in commands:
        name = _NAME_RE.sub("", str(c.command).lower())[:_MAX_NAME]
        if not name:
            log.telegram.warning(
                "[telegram] commands: dropped uncoercible command name",
                extra={"_fields": {"raw": str(c.command)}},
            )
            continue
        if name in seen:
            log.telegram.warning(
                "[telegram] commands: duplicate command name skipped",
                extra={"_fields": {"name": name}},
            )
            continue
        seen.add(name)
        desc = (str(getattr(c, "description", "")) or name)[:_MAX_DESC]
        out.append(BotCommand(name, desc))
    return out


async def register_commands(bot: Any, commands: list[Any]) -> None:
    """Push the command menu to Telegram. Never raises (best-effort, B5-logged)."""
    bot_commands = build_bot_commands(commands)
    if not bot_commands:
        log.telegram.warning("[telegram] commands: nothing to register")
        return
    try:
        await bot.set_my_commands(bot_commands)
        log.telegram.info(
            "[telegram] commands: registered",
            extra={"_fields": {"count": len(bot_commands)}},
        )
    except Exception as exc:  # B5 — registration failure must not block startup
        log.telegram.error(
            "[telegram] commands: set_my_commands failed",
            exc_info=exc, extra={"_fields": {"count": len(bot_commands)}},
        )
