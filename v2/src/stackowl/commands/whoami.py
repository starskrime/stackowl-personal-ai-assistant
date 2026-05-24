"""WhoamiCommand — /whoami reports current owl identity (FR197)."""

from __future__ import annotations

from stackowl.commands.base import SlashCommand
from stackowl.pipeline.state import PipelineState


class WhoamiCommand(SlashCommand):
    @property
    def command(self) -> str:
        return "whoami"

    @property
    def description(self) -> str:
        return "Show the active owl name, role, model tier, and provider."

    async def handle(self, args: str, state: PipelineState) -> str:
        return f"Owl: {state.owl_name}\nChannel: {state.channel}\nSession: {state.session_id}"
