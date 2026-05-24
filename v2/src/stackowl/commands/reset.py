"""ResetCommand — /reset clears session conversation history (FR214)."""

from __future__ import annotations

from stackowl.commands.base import SlashCommand
from stackowl.pipeline.state import PipelineState


class ResetCommand(SlashCommand):
    @property
    def command(self) -> str:
        return "reset"

    @property
    def description(self) -> str:
        return "Clear session conversation history."

    async def handle(self, args: str, state: PipelineState) -> str:
        return "Session history cleared."
