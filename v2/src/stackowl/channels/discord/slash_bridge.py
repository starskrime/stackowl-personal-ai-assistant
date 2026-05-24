"""DiscordSlashCommandBridge — routes Discord interactions to SlashCommandRouter.

Structural stub: the fully-wired interaction handler requires a running bot
session, which we cannot establish during tests. The bridge therefore logs
its routing decision and delegates to :class:`CommandRegistry` exactly the
same way the CLI adapter does.
"""

from __future__ import annotations

from typing import Any

from stackowl.channels.discord.helpers import hash_user_id
from stackowl.commands.registry import CommandRegistry
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import CommandNotFoundError
from stackowl.infra.observability import log
from stackowl.tui.i18n import localize


class DiscordSlashCommandBridge:
    """Bridges discord.py ``Interaction`` objects into the StackOwl command bus."""

    def __init__(self, registry: CommandRegistry | None = None) -> None:
        self._registry = registry or CommandRegistry.instance()
        log.discord.debug(
            "[discord] slash_bridge.init: ready",
            extra={"_fields": {"commands": len(self._registry.list())}},
        )

    async def handle_interaction(self, interaction: Any) -> None:
        """Route a Discord interaction to the matching slash command.

        Expected interaction shape (duck-typed to ease testing without
        discord.py): ``interaction.data["name"]``, ``interaction.data.get("options")``,
        ``interaction.user.id``, ``interaction.response.send_message(text)``.
        """
        log.discord.debug("[discord] slash_bridge.handle_interaction: entry")

        data = getattr(interaction, "data", None) or {}
        name = data.get("name") if isinstance(data, dict) else None
        if not isinstance(name, str) or not name:
            log.discord.warning(
                "[discord] slash_bridge.handle_interaction: missing command name",
                extra={"_fields": {"data_type": type(data).__name__}},
            )
            return

        args = _extract_args(data)
        user_id = getattr(getattr(interaction, "user", None), "id", 0) or 0
        log.discord.debug(
            "[discord] slash_bridge.handle_interaction: decision dispatch",
            extra={"_fields": {"command": name, "user_hash": hash_user_id(int(user_id))}},
        )

        TestModeGuard.assert_not_test_mode("discord.slash_bridge.dispatch")

        try:
            response = await self._registry.dispatch(name, args, _empty_state())
        except CommandNotFoundError as exc:
            log.discord.warning(
                "[discord] slash_bridge.handle_interaction: unknown command",
                extra={"_fields": {"command": name, "err": str(exc)}},
            )
            await _send_interaction(interaction, localize("discord.command.not_found"))
            return
        except Exception as exc:
            log.discord.error(
                "[discord] slash_bridge.handle_interaction: dispatch failed",
                exc_info=exc,
                extra={"_fields": {"command": name}},
            )
            await _send_interaction(interaction, localize("discord.command.error"))
            return

        await _send_interaction(interaction, response)
        log.discord.debug(
            "[discord] slash_bridge.handle_interaction: exit",
            extra={"_fields": {"command": name, "response_len": len(response)}},
        )


def _extract_args(data: dict[str, Any]) -> str:
    options = data.get("options")
    if not isinstance(options, list):
        return ""
    fragments: list[str] = []
    for opt in options:
        if not isinstance(opt, dict):
            continue
        value = opt.get("value")
        if value is not None:
            fragments.append(str(value))
    return " ".join(fragments)


async def _send_interaction(interaction: Any, text: str) -> None:
    response = getattr(interaction, "response", None)
    if response is None or not hasattr(response, "send_message"):
        log.discord.warning(
            "[discord] slash_bridge._send_interaction: no response channel",
            extra={"_fields": {"text_len": len(text)}},
        )
        return
    try:
        await response.send_message(text)
    except Exception as exc:
        log.discord.error(
            "[discord] slash_bridge._send_interaction: send failed",
            exc_info=exc,
            extra={"_fields": {"text_len": len(text)}},
        )


def _empty_state() -> Any:
    """Build a minimal PipelineState carrier; deferred import to avoid cycles."""
    from stackowl.pipeline.state import PipelineState

    return PipelineState(
        trace_id="discord-slash",
        session_id="discord",
        input_text="",
        channel="discord",
        owl_name="",
        pipeline_step="slash_command",
    )
