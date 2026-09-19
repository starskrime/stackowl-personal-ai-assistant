"""``CommandHandlerRegistry`` -- the closed, explicit dict of deterministic
handlers (Story 4.3, AD-1/AD-26).

A DICT, never ``getattr``/``eval``/``exec``-based dynamic dispatch -- AD-1's
Boundaries forbid exactly that shape, and
``tests/commands/spec/test_one_door_tripwires.py`` scans this module's own
source to prove it.
"""

from __future__ import annotations

from stackowl.commands.spec.command_spec import CommandHandler
from stackowl.commands.spec.errors import CommandTypeNotDeclaredError

# AD-7 — see registry.py's identical import for why `log` comes from here.
from stackowl.infra.observability import log


class CommandHandlerRegistry:
    """Process-wide singleton table of every declared type's handler."""

    _handlers: dict[str, CommandHandler] = {}

    @classmethod
    def register(cls, command_type: str, handler: CommandHandler) -> CommandHandler:
        if command_type in cls._handlers:
            raise ValueError(
                f"a handler is already registered for {command_type!r} -- a "
                "second registration would mean the same command type runs "
                "two different implementations"
            )
        cls._handlers[command_type] = handler
        log.gateway.debug(
            "[commands] CommandHandlerRegistry.register: registered",
            extra={"_fields": {"command_type": command_type}},
        )
        return handler

    @classmethod
    def get(cls, command_type: str) -> CommandHandler:
        handler = cls._handlers.get(command_type)
        if handler is None:
            raise CommandTypeNotDeclaredError(command_type)
        return handler

    @classmethod
    def reset(cls) -> None:
        """Clear every registration -- test use only."""
        cls._handlers.clear()
