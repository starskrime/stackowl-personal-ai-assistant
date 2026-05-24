"""FocusCommand — ``/focus`` slash command (Story 7.4).

Sets the in-memory focus mode on the active :class:`NotificationRouter` and
emits a ``focus_mode_changed`` event for downstream subscribers.

Modes:

* ``/focus``         → ``soft`` (batch normal+low)
* ``/focus --hard``  → ``hard`` (batch normal, suppress low)
* ``/focus off``     → ``off``  (deliver everything)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.infra.observability import log
from stackowl.notifications.router import FocusMode, NotificationRouter

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.events.bus import EventBus
    from stackowl.pipeline.state import PipelineState


_EVENT_FOCUS_CHANGED = "focus_mode_changed"


class FocusCommand(SlashCommand):
    """Set the in-memory focus mode for the notification router."""

    def __init__(self, router: NotificationRouter, event_bus: EventBus) -> None:
        self._router = router
        self._event_bus = event_bus

    @property
    def command(self) -> str:
        return "focus"

    @property
    def description(self) -> str:
        return "Control focus mode (suppress/batch notifications)."

    async def handle(self, args: str, state: PipelineState) -> str:
        log.notifications.debug(
            "[notifications] focus.handle: entry",
            extra={"_fields": {"args": args[:40], "session": state.session_id}},
        )
        stripped = args.strip()
        mode: FocusMode
        if stripped == "--hard":
            mode = "hard"
        elif stripped == "off":
            mode = "off"
        else:
            mode = "soft"

        log.notifications.debug(
            "[notifications] focus.handle: decision",
            extra={"_fields": {"mode": mode}},
        )
        self._router.set_focus_mode(mode)
        try:
            self._event_bus.emit(_EVENT_FOCUS_CHANGED, {"mode": mode})
        except Exception as exc:  # B5 — never silent
            log.notifications.warning(
                "[notifications] focus.handle: event emit failed",
                exc_info=exc,
                extra={"_fields": {"event": _EVENT_FOCUS_CHANGED, "mode": mode}},
            )
        log.notifications.info(
            "[notifications] focus.handle: exit",
            extra={"_fields": {"mode": mode}},
        )
        return f"focus_mode:{mode}"

    @classmethod
    def create_and_register(
        cls, router: NotificationRouter, event_bus: EventBus
    ) -> FocusCommand:
        """Construct a :class:`FocusCommand` and register it on the singleton."""
        cmd = cls(router=router, event_bus=event_bus)
        CommandRegistry.instance().register(cmd)
        return cmd
