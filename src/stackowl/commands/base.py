"""SlashCommand ABC — base contract for all slash commands."""

from __future__ import annotations

from abc import ABC, abstractmethod

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

    @abstractmethod
    async def handle(self, args: str, state: PipelineState) -> str:
        """Execute and return a response string."""
        ...
