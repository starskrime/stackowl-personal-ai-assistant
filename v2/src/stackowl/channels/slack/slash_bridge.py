"""SlackSlashCommandBridge — translate Slack slash commands into platform dispatches.

This is a structural shim. The real Slack ``ack()`` / ``respond()`` calls
belong to the caller that owns the live ``AsyncApp``; the bridge focuses on
looking up the platform command and returning text the caller can hand back
to Slack verbatim.
"""

from __future__ import annotations

import time
import uuid

from stackowl.commands.registry import CommandRegistry
from stackowl.exceptions import CommandNotFoundError, CommandParseError
from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState
from stackowl.tui.i18n import localize

from .helpers import hash_user_id


class SlackSlashCommandBridge:
    """Resolve a Slack slash command against the platform CommandRegistry."""

    def __init__(self, registry: CommandRegistry | None = None) -> None:
        self._registry = registry or CommandRegistry.instance()
        log.slack.debug(
            "[slack] slash_bridge.init: entry",
            extra={"_fields": {"registered_count": len(self._registry.list())}},
        )

    async def handle_slash_command(
        self, command: str, text: str, user_id: str
    ) -> str:
        """Dispatch ``/command text`` and return the response text or error.

        The Slack payload uses raw command strings like ``"/memory"`` — we
        strip the leading slash before consulting the registry. Errors map to
        localized strings so users never see a raw stack trace.
        """
        start = time.perf_counter()
        log.slack.debug(
            "[slack] slash_bridge.handle_slash_command: entry",
            extra={
                "_fields": {
                    "command": command,
                    "user_hash": hash_user_id(user_id),
                    "text_len": len(text),
                }
            },
        )
        name = command.lstrip("/")
        log.slack.debug(
            "[slack] slash_bridge.handle_slash_command: decision normalized",
            extra={"_fields": {"name": name}},
        )

        state = PipelineState(
            trace_id=f"slack-slash-{uuid.uuid4().hex[:12]}",
            session_id=f"slack:{hash_user_id(user_id)}",
            input_text=f"/{name} {text}".strip(),
            channel="slack",
            owl_name="secretary",
            pipeline_step="slash_command",
        )

        try:
            result = await self._registry.dispatch(name, text, state)
        except CommandNotFoundError as err:
            log.slack.warning(
                "[slack] slash_bridge.handle_slash_command: unknown command",
                extra={"_fields": {"name": name, "err": str(err)}},
            )
            return localize("slack.slash.unknown_command", lang="en") + f" /{name}"
        except CommandParseError as err:
            log.slack.warning(
                "[slack] slash_bridge.handle_slash_command: parse error",
                extra={"_fields": {"name": name, "err": str(err)}},
            )
            return localize("slack.slash.parse_error", lang="en") + f": {err}"
        except Exception as err:  # noqa: BLE001
            # Defensive: never let a command crash propagate into Slack — log
            # and return a localized fallback message instead.
            log.slack.error(
                "[slack] slash_bridge.handle_slash_command: dispatch failed",
                exc_info=err,
                extra={"_fields": {"name": name}},
            )
            return localize("slack.slash.internal_error", lang="en")

        duration_ms = (time.perf_counter() - start) * 1000.0
        log.slack.debug(
            "[slack] slash_bridge.handle_slash_command: exit",
            extra={
                "_fields": {
                    "name": name,
                    "result_len": len(result),
                    "duration_ms": duration_ms,
                }
            },
        )
        return result
