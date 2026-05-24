"""TelegramSlashCommandBridge — routes Telegram /commands to SlashCommandRouter.

Structural delegate: the bridge routes ``/command args`` text to the
:class:`CommandRegistry` exactly the same way the Discord adapter does.
"""

from __future__ import annotations

from typing import Any

from stackowl.channels.telegram.helpers import hash_user_id
from stackowl.commands.registry import CommandRegistry
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import CommandNotFoundError
from stackowl.infra.observability import log
from stackowl.tui.i18n import localize


class TelegramSlashCommandBridge:
    """Bridges Telegram text commands into the StackOwl command bus."""

    def __init__(self, registry: CommandRegistry | None = None) -> None:
        self._registry = registry or CommandRegistry.instance()
        log.telegram.debug(
            "[telegram] slash_bridge.init: ready",
            extra={"_fields": {"commands": len(self._registry.list())}},
        )

    async def handle(
        self,
        text: str,
        user_id: int,
        context_data: dict[str, Any] | None = None,
    ) -> str:
        """Route ``/command args`` text to the matching slash command.

        4-point logging: entry / decision / step / exit.

        Args:
            text: Raw slash command text starting with ``/``.
            user_id: Telegram user ID of the sender (never logged raw).
            context_data: Optional extra context forwarded to the handler.

        Returns:
            Human-readable command response string.
        """
        log.telegram.debug(
            "[telegram] slash_bridge.handle: entry",
            extra={"_fields": {"user_hash": hash_user_id(user_id)}},
        )

        parts = text.lstrip("/").split(None, 1)
        command_name = parts[0] if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        if not command_name:
            log.telegram.warning(
                "[telegram] slash_bridge.handle: empty command name",
                extra={"_fields": {"user_hash": hash_user_id(user_id)}},
            )
            return localize("telegram.command.empty")

        log.telegram.debug(
            "[telegram] slash_bridge.handle: decision dispatch",
            extra={
                "_fields": {
                    "command": command_name,
                    "user_hash": hash_user_id(user_id),
                }
            },
        )
        TestModeGuard.assert_not_test_mode("telegram.slash_bridge.dispatch")

        ctx: dict[str, Any] = {"channel": "telegram", "user_id": user_id}
        if context_data:
            ctx.update(context_data)

        state = _build_state(command_name, user_id)
        try:
            response = await self._registry.dispatch(command_name, args, state)
        except CommandNotFoundError as exc:
            log.telegram.warning(
                "[telegram] slash_bridge.handle: unknown command",
                extra={"_fields": {"command": command_name, "err": str(exc)}},
            )
            return localize("telegram.command.not_found")
        except Exception as exc:
            log.telegram.error(
                "[telegram] slash_bridge.handle: dispatch failed",
                exc_info=exc,
                extra={"_fields": {"command": command_name}},
            )
            return localize("telegram.command.error")

        log.telegram.debug(
            "[telegram] slash_bridge.handle: exit",
            extra={"_fields": {"command": command_name, "response_len": len(response)}},
        )
        return response


def _build_state(command_name: str, user_id: int) -> Any:
    """Build a minimal PipelineState for slash command dispatch."""
    from stackowl.pipeline.state import PipelineState

    return PipelineState(
        trace_id=f"telegram-slash-{user_id}",
        session_id=str(user_id),
        input_text="",
        channel="telegram",
        owl_name="",
        pipeline_step="slash_command",
    )
