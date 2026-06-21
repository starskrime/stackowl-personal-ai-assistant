"""SlashCommand ABC — base contract for all slash commands."""

from __future__ import annotations

from abc import ABC, abstractmethod

from stackowl.commands.metadata import CommandMeta
from stackowl.pipeline.state import PipelineState


class SlashCommand(ABC):
    """Abstract base for all slash commands (built-in and plugin)."""

    @property
    @abstractmethod
    def command(self) -> str:
        """The slash command name without '/'."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line help text shown by /help."""
        ...

    @property
    def meta(self) -> CommandMeta:
        """Structured sub-command metadata for this command.

        The default is empty, so every existing command satisfies the contract
        with no edits and behaves byte-for-byte as before.  A command opts in by
        overriding this property with a populated :class:`CommandMeta`, which
        then drives autocomplete, ``/help``, and auto-generated usage.
        """
        return CommandMeta()

    @abstractmethod
    async def handle(self, args: str, state: PipelineState) -> str:
        """Execute and return a response string."""
        ...
