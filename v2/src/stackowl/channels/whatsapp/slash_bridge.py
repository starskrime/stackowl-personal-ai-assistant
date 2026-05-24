"""WhatsAppSlashCommandBridge — routes /command messages to CommandRegistry.

When the user sends a message starting with ``/`` via WhatsApp, this bridge
parses the command name and arguments and dispatches to the StackOwl command
bus — the same pattern used by Discord and Slack bridges.
"""

from __future__ import annotations

from typing import Any

from stackowl.channels.whatsapp.helpers import hash_jid
from stackowl.commands.registry import CommandRegistry
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import CommandNotFoundError
from stackowl.infra.observability import log
from stackowl.tui.i18n import localize


class WhatsAppSlashCommandBridge:
    """Bridges WhatsApp text messages that begin with ``/`` into the command bus."""

    def __init__(self, registry: CommandRegistry | None = None) -> None:
        self._registry = registry or CommandRegistry.instance()
        log.whatsapp.debug(
            "[whatsapp] slash_bridge.init: ready",
            extra={"_fields": {"commands": len(self._registry.list())}},
        )

    async def handle_message(self, jid: str, text: str) -> str:
        """Parse and dispatch a ``/command [args]`` message from WhatsApp.

        Args:
            jid: Sender's WhatsApp JID (used only for hashed logging).
            text: Raw message text starting with ``/``.

        Returns:
            Response string to send back to the user.

        4-point logging: entry / decision / step / exit.
        """
        log.whatsapp.debug(
            "[whatsapp] slash_bridge.handle_message: entry",
            extra={"_fields": {"jid_hash": hash_jid(jid)}},
        )

        if not text.startswith("/"):
            log.whatsapp.debug(
                "[whatsapp] slash_bridge.handle_message: not a command — ignoring"
            )
            return ""

        parts = text[1:].split(None, 1)
        name = parts[0] if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        log.whatsapp.debug(
            "[whatsapp] slash_bridge.handle_message: decision dispatch",
            extra={"_fields": {"command": name, "jid_hash": hash_jid(jid)}},
        )

        TestModeGuard.assert_not_test_mode("whatsapp.slash_bridge.dispatch")

        try:
            response: str = await self._registry.dispatch(name, args, _empty_state())
        except CommandNotFoundError as exc:
            log.whatsapp.warning(
                "[whatsapp] slash_bridge.handle_message: unknown command",
                extra={"_fields": {"command": name, "err": str(exc)}},
            )
            return localize("whatsapp.command.not_found")
        except Exception as exc:
            log.whatsapp.error(
                "[whatsapp] slash_bridge.handle_message: dispatch failed",
                exc_info=exc,
                extra={"_fields": {"command": name}},
            )
            return localize("whatsapp.command.error")

        log.whatsapp.debug(
            "[whatsapp] slash_bridge.handle_message: exit",
            extra={"_fields": {"command": name, "response_len": len(response)}},
        )
        return response


def _empty_state() -> Any:
    """Build a minimal PipelineState carrier; deferred import to avoid cycles."""
    from stackowl.pipeline.state import PipelineState

    return PipelineState(
        trace_id="whatsapp-slash",
        session_id="whatsapp",
        input_text="",
        channel="whatsapp",
        owl_name="",
        pipeline_step="slash_command",
    )
