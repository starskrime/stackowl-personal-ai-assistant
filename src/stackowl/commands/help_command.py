"""HelpCommand — /help slash command listing all registered commands.

Pulls from :class:`CommandRegistry` so plugin-supplied commands appear without
any code change here.
"""

from __future__ import annotations

from stackowl.commands.base import SlashCommand
from stackowl.commands.help_render import (
    render_command_page,
    render_index,
    render_subcommand_page,
)
from stackowl.commands.metadata import resolve_path
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
        """Three rungs of progressive disclosure:

        * ``/help``                 → grouped command index
        * ``/help <command>``       → that command's page (one level of subs)
        * ``/help <command> <sub>`` → the sub-command's full page
        """
        log.gateway.debug(
            "[commands] help.handle: entry",
            extra={"_fields": {"session": state.session_id, "args_len": len(args)}},
        )
        registry = CommandRegistry.instance()
        tokens = args.split()

        # Rung 1 — index.
        if not tokens:
            cmds = registry.list()
            log.gateway.debug(
                "[commands] help.handle: index", extra={"_fields": {"count": len(cmds)}}
            )
            return render_index(cmds)

        # Strip a leading slash a user may have typed: `/help /memory`.
        cmd_name = tokens[0].lstrip("/")
        cmd = registry.get(cmd_name)
        if cmd is None:
            log.gateway.debug(
                "[commands] help.handle: unknown command",
                extra={"_fields": {"name": cmd_name}},
            )
            return f"Unknown command: '/{cmd_name}'. Try /help to see what's available."

        # Rung 2 — command page.
        sub_path = tokens[1:]
        if not sub_path:
            return render_command_page(cmd)

        # Rung 3 — sub-command page (resolves N levels).
        node = resolve_path(cmd.meta.subcommands, sub_path)
        if node is None:
            log.gateway.debug(
                "[commands] help.handle: unknown subcommand — falling back to command page",
                extra={"_fields": {"name": cmd_name, "path": sub_path}},
            )
            unknown = " ".join(sub_path)
            return (
                f"Unknown sub-command: '/{cmd_name} {unknown}'.\n\n"
                + render_command_page(cmd)
            )
        return render_subcommand_page(cmd_name, sub_path, node)


_CMD = register_command(HelpCommand())
