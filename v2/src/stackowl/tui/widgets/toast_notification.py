"""ToastNotification — urgency-tiered transient banner with auto-dismiss."""

from __future__ import annotations

import os
from typing import Literal

from textual.binding import Binding
from textual.widget import Widget

from stackowl.infra.observability import log

Urgency = Literal["low", "normal", "critical"]

_AUTO_DISMISS_SECONDS: dict[str, float | None] = {
    "low": 3.0,
    "normal": 6.0,
    "critical": None,  # critical never auto-dismisses
}


class ToastNotification(Widget):
    """Urgency-tiered toast with auto-dismiss (critical stays until dismissed)."""

    DEFAULT_CSS = """
    ToastNotification {
        layer: overlay;
        width: 40;
        background: $color-surface;
        border: solid $color-border;
        color: $color-text-primary;
        height: auto;
    }
    ToastNotification.-critical {
        background: $color-error;
        color: $color-text-primary;
        border: solid $color-error;
    }
    ToastNotification.-low {
        color: $color-text-muted;
    }
    """

    BINDINGS = [
        Binding("d", "dismiss", "Dismiss"),
        Binding("enter", "dismiss", "Dismiss"),
    ]

    def __init__(self, message: str, urgency: Urgency = "normal") -> None:
        super().__init__(classes=f"-{urgency}")
        log.tui.debug(
            "[tui] toast_notification.__init__: entry",
            extra={"_fields": {"urgency": urgency, "message_len": len(message)}},
        )
        self._message: str = message
        self._urgency: Urgency = urgency
        self._reduced_motion: bool = (
            os.environ.get("STACKOWL_REDUCED_MOTION") == "1"
        )
        self._auto_dismiss_seconds: float | None = _AUTO_DISMISS_SECONDS.get(
            urgency, _AUTO_DISMISS_SECONDS["normal"]
        )

    @property
    def message(self) -> str:
        return self._message

    @property
    def urgency(self) -> Urgency:
        return self._urgency

    @property
    def auto_dismiss_seconds(self) -> float | None:
        return self._auto_dismiss_seconds

    @property
    def reduced_motion(self) -> bool:
        return self._reduced_motion

    def on_mount(self) -> None:
        """Schedule auto-dismiss unless this is a critical toast."""
        log.tui.debug(
            "[tui] toast_notification.on_mount: entry",
            extra={
                "_fields": {
                    "urgency": self._urgency,
                    "auto_dismiss": self._auto_dismiss_seconds,
                }
            },
        )
        if self._auto_dismiss_seconds is None:
            return
        try:
            self.set_timer(self._auto_dismiss_seconds, self._auto_dismiss)
        except Exception as exc:
            log.tui.warning(
                "[tui] toast_notification.on_mount: set_timer failed",
                exc_info=exc,
                extra={"_fields": {"urgency": self._urgency}},
            )

    def render(self) -> str:
        return self._message

    def _auto_dismiss(self) -> None:
        """Timer callback — remove the widget from the DOM."""
        log.tui.debug(
            "[tui] toast_notification._auto_dismiss: entry",
            extra={"_fields": {"urgency": self._urgency}},
        )
        self._safe_remove()

    def action_dismiss(self) -> None:
        """Binding action — dismiss the toast immediately."""
        log.tui.debug(
            "[tui] toast_notification.action_dismiss: entry",
            extra={"_fields": {"urgency": self._urgency}},
        )
        self._safe_remove()

    def _safe_remove(self) -> None:
        try:
            self.remove()
        except Exception as exc:
            log.tui.warning(
                "[tui] toast_notification._safe_remove: remove failed",
                exc_info=exc,
                extra={"_fields": {"urgency": self._urgency}},
            )
