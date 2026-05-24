"""CommandRegistry — singleton dispatch table for all slash commands."""

from __future__ import annotations

from stackowl.commands.base import SlashCommand
from stackowl.exceptions import CommandNotFoundError
from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState


class CommandRegistry:
    """Singleton registry; open for extension — plugins call register() at import time."""

    _instance: CommandRegistry | None = None

    def __init__(self) -> None:
        self._commands: dict[str, SlashCommand] = {}
        self._source_map: dict[str, list[str]] = {}

    @classmethod
    def instance(cls) -> CommandRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton — test use only."""
        cls._instance = None

    def register(self, command: SlashCommand, source_name: str | None = None) -> None:
        self._commands[command.command] = command
        if source_name:
            self._source_map.setdefault(source_name, []).append(command.command)
        log.gateway.debug(
            "[commands] registry.register: command registered",
            extra={"_fields": {"command": command.command, "source": source_name}},
        )

    def unregister_by_source(self, source_name: str) -> int:
        """Remove all commands registered under source_name. Returns count removed."""
        log.gateway.debug(
            "[commands] registry.unregister_by_source: entry",
            extra={"_fields": {"source": source_name}},
        )
        names = self._source_map.pop(source_name, [])
        for name in names:
            self._commands.pop(name, None)
        log.gateway.debug(
            "[commands] registry.unregister_by_source: exit",
            extra={"_fields": {"source": source_name, "removed": len(names)}},
        )
        return len(names)

    async def dispatch(self, name: str, args: str, state: PipelineState) -> str:
        if name not in self._commands:
            raise CommandNotFoundError(name)
        log.gateway.debug(
            "[commands] registry.dispatch: dispatching",
            extra={"_fields": {"command": name, "args": args[:80]}},
        )
        return await self._commands[name].handle(args, state)

    def list(self) -> list[SlashCommand]:
        return sorted(self._commands.values(), key=lambda c: c.command)


def register_command(cmd: SlashCommand) -> SlashCommand:
    """Decorator-style helper: register a SlashCommand at import time."""
    CommandRegistry.instance().register(cmd)
    return cmd
