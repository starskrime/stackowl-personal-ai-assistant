"""WhoamiCommand — /whoami reports current owl identity (FR197)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.pipeline.state import PipelineState

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.owls.registry import OwlRegistry


class WhoamiCommand(SlashCommand):
    def __init__(self, owl_registry: OwlRegistry | None = None) -> None:
        self._owl_registry = owl_registry

    @property
    def command(self) -> str:
        return "whoami"

    @property
    def description(self) -> str:
        return "Show the active owl name, role, model tier, and provider."

    async def handle(self, args: str, state: PipelineState) -> str:
        lines = [
            f"Owl: {state.owl_name}",
            f"Channel: {state.channel}",
            f"Session: {state.session_id}",
        ]
        if self._owl_registry is not None:
            try:
                manifest = self._owl_registry.get(state.owl_name)
                lines.append(f"Role: {manifest.role}")
                lines.append(f"Model tier: {manifest.model_tier}")
                if manifest.provider_name:
                    lines.append(f"Provider: {manifest.provider_name}")
            except Exception:
                pass  # registry lookup failed — show what we have
        return "\n".join(lines)
