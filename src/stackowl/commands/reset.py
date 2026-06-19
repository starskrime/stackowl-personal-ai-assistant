"""ResetCommand — /reset clears session conversation history (FR214)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.pipeline.state import PipelineState


class ResetCommand(SlashCommand):
    """``/reset`` — delete all conversation turns for the current session.

    Requires a :class:`MemoryBridge` to perform the actual deletion.  When no
    bridge is configured, emits an honest error rather than silently lying.
    """

    def __init__(self, bridge: MemoryBridge | None = None) -> None:
        # 1. ENTRY
        log.gateway.debug("[commands] reset.init: entry")
        self._bridge = bridge
        # 4. EXIT
        log.gateway.debug("[commands] reset.init: exit")

    @property
    def command(self) -> str:
        return "reset"

    @property
    def description(self) -> str:
        return "Clear session conversation history."

    async def handle(self, args: str, state: PipelineState) -> str:
        """Execute /reset — delete conversation turns for state.session_id."""
        # 1. ENTRY
        log.gateway.debug(
            "[commands] reset.handle: entry",
            extra={"_fields": {"session_id": state.session_id}},
        )
        # 2. DECISION — refuse honestly when not configured
        if self._bridge is None:
            log.gateway.warning("[commands] reset.handle: bridge not configured")
            return "✗ /reset: not configured"

        # 3. STEP — delegate to bridge
        try:
            count = await self._bridge.clear_session(state.session_id)
        except Exception as exc:
            log.gateway.error("[commands] reset.handle: clear_session failed", exc_info=exc)
            return f"✗ /reset: {exc}"

        # 4. EXIT — report reality, never a hard-coded lie
        if count == 0:
            result = "Nothing to clear — no conversation turns for this session."
        else:
            result = f"Cleared {count} conversation turn(s) for this session."
        log.gateway.info(
            "[commands] reset.handle: exit",
            extra={"_fields": {"session_id": state.session_id, "deleted": count}},
        )
        return result
