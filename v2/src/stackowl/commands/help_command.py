"""HelpCommand — /help slash command listing all registered commands.

Pulls from :class:`CommandRegistry` so plugin-supplied commands appear without
any code change here.
"""

from __future__ import annotations

from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import CommandRegistry, register_command
from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState


class HelpCommand(SlashCommand):
    @property
    def command(self) -> str:
        return "help"

    @property
    def description(self) -> str:
        return "List all available slash commands."

    async def handle(self, args: str, state: PipelineState) -> str:
        log.gateway.debug(
            "[commands] help.handle: entry",
            extra={"_fields": {"session": state.session_id}},
        )
        cmds = CommandRegistry.instance().list()
        log.gateway.debug(
            "[commands] help.handle: collected commands",
            extra={"_fields": {"count": len(cmds)}},
        )
        if not cmds:
            return "(no commands registered)"
        lines = ["Available commands:", ""]
        for cmd in cmds:
            lines.append(f"  /{cmd.command:<20} {cmd.description}")
        log.gateway.debug("[commands] help.handle: exit", extra={"_fields": {"count": len(cmds)}})
        return "\n".join(lines)


_CMD = register_command(HelpCommand())
