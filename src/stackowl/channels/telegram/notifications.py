"""TelegramNotificationDispatcher — routes proactive heartbeat events to Telegram.

Dispatches morning briefs, parliament syntheses, evolution badges, and memory
nudges to the configured Telegram adapter. Quiet hours and evolution suppression
are enforced before delivery. Message content is never logged — only a
sha256[:16] hash is recorded for traceability.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

from stackowl.channels.telegram.quiet_hours import QuietHoursChecker
from stackowl.infra.observability import log
from stackowl.tui.i18n import localize

if TYPE_CHECKING:
    from stackowl.channels.telegram.adapter import TelegramChannelAdapter
    from stackowl.channels.telegram.formatter import (
        TelegramBriefFormatter,
        TelegramEvolutionFormatter,
        TelegramMemoryFormatter,
        TelegramParliamentFormatter,
    )
    from stackowl.channels.telegram.settings import TelegramSettings


def _content_hash(text: str) -> str:
    """Return the first 16 hex chars of SHA-256(text) — safe for logging."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class NotificationPayload(BaseModel):
    """Immutable descriptor for a single proactive notification.

    Attributes:
        event_type: Classification of the heartbeat event.
        content: Event-type-specific payload; keys vary by event_type.
        urgency: Delivery priority; critical bypasses quiet hours.
        lang: BCP-47 language tag forwarded to ``localize()``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: Literal[
        "morning_brief", "parliament_synthesis", "evolution", "memory_nudge", "custom"
    ]
    content: dict[str, object]
    urgency: Literal["low", "normal", "critical"] = "normal"
    lang: str = "en"


class TelegramNotificationDispatcher:
    """Central dispatcher for proactive notifications to a Telegram channel."""

    def __init__(
        self,
        adapter: TelegramChannelAdapter,
        quiet_hours: QuietHoursChecker,
        formatters: dict[str, Any],
        settings: TelegramSettings | None = None,
    ) -> None:
        self._adapter = adapter
        self._quiet_hours = quiet_hours
        self._formatters = formatters
        self._settings = settings
        log.telegram.debug(
            "[telegram] dispatcher.init: entry",
            extra={
                "_fields": {
                    "formatter_keys": list(formatters.keys()),
                    "has_settings": settings is not None,
                }
            },
        )

    async def dispatch(self, payload: NotificationPayload) -> None:
        """Route a notification payload to the Telegram adapter.

        4-point logging: entry / decision / step / exit.

        Args:
            payload: The :class:`NotificationPayload` describing the event.
        """
        log.telegram.debug(
            "[telegram] dispatcher.dispatch: entry",
            extra={
                "_fields": {
                    "event_type": payload.event_type,
                    "urgency": payload.urgency,
                    "lang": payload.lang,
                }
            },
        )

        # Quiet-hours gate
        if self._quiet_hours.should_suppress(payload.urgency):
            log.telegram.debug(
                "[telegram] dispatcher.dispatch: decision suppressed — quiet hours active",
                extra={"_fields": {"event_type": payload.event_type}},
            )
            return

        log.telegram.debug(
            "[telegram] dispatcher.dispatch: decision routing",
            extra={"_fields": {"event_type": payload.event_type}},
        )

        formatted: str | None = None
        keyboard: dict[str, object] | None = None

        try:
            if payload.event_type == "morning_brief":
                formatter: TelegramBriefFormatter = self._formatters["brief"]
                sections = {str(k): str(v) for k, v in payload.content.items()}
                formatted = formatter.format_morning_brief(sections)

            elif payload.event_type == "parliament_synthesis":
                formatter_parl: TelegramParliamentFormatter = self._formatters["parliament"]
                synthesis = str(payload.content.get("synthesis", ""))
                owl_names = list(payload.content.get("owl_names", []))  # type: ignore[arg-type]
                round_count = int(payload.content.get("round_count", 0))
                formatted = formatter_parl.format_synthesis(synthesis, owl_names, round_count)

            elif payload.event_type == "evolution":
                suppress_evo = (
                    self._settings.suppress_evolution_events
                    if self._settings is not None
                    else False
                )
                if suppress_evo:
                    log.telegram.debug(
                        "[telegram] dispatcher.dispatch: decision evolution suppressed by settings",
                    )
                    return
                formatter_evo: TelegramEvolutionFormatter = self._formatters["evolution"]
                owl_name = str(payload.content.get("owl_name", ""))
                raw_deltas = payload.content.get("trait_deltas", {})
                trait_deltas = {str(k): float(v) for k, v in raw_deltas.items()}  # type: ignore[union-attr]
                formatted = formatter_evo.format_evolution_event(owl_name, trait_deltas)

            elif payload.event_type == "memory_nudge":
                formatter_mem: TelegramMemoryFormatter = self._formatters["memory"]
                fact_content = str(payload.content.get("fact_content", ""))
                fact_id = str(payload.content.get("fact_id", ""))
                formatted, keyboard = formatter_mem.format_memory_nudge(fact_content, fact_id)

            elif payload.event_type == "custom":
                text = str(payload.content.get("text", ""))
                formatted = text

        except Exception as exc:
            log.telegram.error(
                "[telegram] dispatcher.dispatch: formatting failed",
                exc_info=exc,
                extra={"_fields": {"event_type": payload.event_type}},
            )
            return

        if formatted is None:
            log.telegram.debug(
                "[telegram] dispatcher.dispatch: no formatted content — skip send",
                extra={"_fields": {"event_type": payload.event_type}},
            )
            return

        # Step: send via adapter
        log.telegram.debug(
            "[telegram] dispatcher.dispatch: step send",
            extra={
                "_fields": {
                    "event_type": payload.event_type,
                    "content_hash": _content_hash(formatted),
                    "has_keyboard": keyboard is not None,
                }
            },
        )

        if keyboard is not None:
            await self._adapter.send_inline_keyboard(formatted, keyboard)
        else:
            await self._adapter.send_text(formatted)

        log.telegram.debug(
            "[telegram] dispatcher.dispatch: exit",
            extra={
                "_fields": {
                    "event_type": payload.event_type,
                    "content_hash": _content_hash(formatted),
                }
            },
        )
