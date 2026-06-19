"""ByeCommand — ``/bye`` gracefully shuts down the StackOwl server.

Trips the orchestrator's cooperative ``stop_event`` (the same event SIGTERM/SIGINT
set), so the normal graceful teardown runs — children terminated, DB handle
closed, sessions drained — instead of a hard kill. Honored from ANY channel
(the user opted for this); a remote ``/bye`` therefore stops the whole process.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.pipeline.state import PipelineState


class ByeCommand(SlashCommand):
    """Implements ``/bye`` — graceful server shutdown."""

    def __init__(self, shutdown_event: asyncio.Event | None = None) -> None:
        self._shutdown_event = shutdown_event

    @property
    def command(self) -> str:
        return "bye"

    @property
    def description(self) -> str:
        return "Gracefully shut down the StackOwl server."

    async def handle(self, args: str, state: PipelineState) -> str:
        log.gateway.info(
            "[commands] bye.handle: shutdown requested",
            extra={"_fields": {"channel": state.channel, "session": state.session_id}},
        )
        if self._shutdown_event is None:
            # Honest degradation — no wire to the orchestrator's stop_event.
            log.gateway.warning("[commands] bye.handle: shutdown_event not configured")
            return "✗ /bye: shutdown is not available in this context."
        self._shutdown_event.set()
        return "Goodbye 👋 — shutting down StackOwl. See you next time."

    @classmethod
    def create_and_register(
        cls, shutdown_event: asyncio.Event | None = None
    ) -> ByeCommand:
        """Construct a :class:`ByeCommand` and register it on the singleton."""
        cmd = cls(shutdown_event=shutdown_event)
        CommandRegistry.instance().register(cmd)
        return cmd
