"""SlashCommand ABC — base contract for all slash commands."""

from __future__ import annotations

from abc import ABC, abstractmethod

from stackowl.commands.metadata import CommandMeta
from stackowl.commands.response import CommandResponse
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
    async def handle(self, args: str, state: PipelineState) -> str | CommandResponse:
        """Execute and return a response string, or a CommandResponse with
        tappable follow-up actions."""
        ...

    def build_turn_prompt(self, args: str) -> str | None:
        """Return text to run AS THIS TURN'S INPUT, or None for a normal command.

        THE SEAM D09.5 NEEDED, and the only new concept it introduces. Almost
        every command ANSWERS the user: it computes something and its text is
        displayed. A few instead want to STEER the agent — to say "go and do
        this" in the agent's own voice, and let the ordinary turn do the work
        with the tools it already has.

        Returning a string here means exactly that: the gateway discards the
        reply path and re-routes the message as though the user had typed this
        text instead of the command. Nothing else changes — same pipeline, same
        routing, same consent, same tools.

        WHY THIS RATHER THAN LETTING THE COMMAND DO THE WORK. A command that
        gathered sources and called a model inside :meth:`handle` would be a
        second engine beside the one that already runs work, which Bakir's
        2026-08-17 rule forbids. This keeps such commands at ladder rung 2: they
        contribute a PROMPT and no machinery.

        THE ONE MEANING IT MUST KEEP. "This text is the turn's input." It is not
        a general "commands may do anything" hook — anything broader becomes a
        second dispatch path, which is the thing it exists to avoid.

        Default None, so every existing command satisfies this with no edit and
        behaves byte-for-byte as before.
        """
        return None
