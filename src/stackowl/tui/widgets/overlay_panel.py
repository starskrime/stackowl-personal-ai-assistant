"""OverlayPanel — base class for stacking modal overlays with focus restoration.

An :class:`OverlayQueue` keeps overlays one-at-a-time so a Memory-Review,
Toast, and Evolution-Inspection request never collide visually.  When a panel
closes it emits :class:`OverlayClosedMessage` so the queue can surface the
next pending overlay.
"""

from __future__ import annotations

import weakref
from collections import deque
from typing import TYPE_CHECKING

from textual import events
from textual.binding import Binding
from textual.widget import Widget

from stackowl.infra.observability import log
from stackowl.tui.messages import OverlayClosedMessage

if TYPE_CHECKING:
    pass


class OverlayPanel(Widget):
    """Base overlay widget — captures prior focus + advertises an escape binding."""

    overlay_name: str = "overlay"

    DEFAULT_CSS = """
    OverlayPanel {
        layer: overlay;
    }
    """

    BINDINGS = [
        Binding("escape", "close_overlay", "Close overlay"),
    ]

    def __init__(self) -> None:
        super().__init__()
        log.tui.debug(
            "[tui] overlay_panel.__init__: entry",
            extra={"_fields": {"overlay_name": self.overlay_name}},
        )
        self._prior_focused: weakref.ref[Widget] | None = None

    def open_overlay(self, prior_focused: Widget | None = None) -> None:
        """Reveal the panel and remember the widget that had focus."""
        log.tui.debug(
            "[tui] overlay_panel.open_overlay: entry",
            extra={
                "_fields": {
                    "overlay_name": self.overlay_name,
                    "has_prior": prior_focused is not None,
                }
            },
        )
        if prior_focused is not None:
            self._prior_focused = weakref.ref(prior_focused)
        self.display = True

    def close(self) -> None:
        """Hide the panel, post :class:`OverlayClosedMessage`, restore focus."""
        log.tui.debug(
            "[tui] overlay_panel.close: entry",
            extra={"_fields": {"overlay_name": self.overlay_name}},
        )
        self.display = False
        try:
            self.post_message(OverlayClosedMessage(overlay_name=self.overlay_name))
        except Exception as exc:
            log.tui.warning(
                "[tui] overlay_panel.close: post_message failed",
                exc_info=exc,
                extra={"_fields": {"overlay_name": self.overlay_name}},
            )
        if self._prior_focused is not None:
            target = self._prior_focused()
            if target is None:
                log.tui.warning(
                    "[tui] overlay_panel.close: prior focused widget already gc'd",
                    extra={"_fields": {"overlay_name": self.overlay_name}},
                )
            else:
                try:
                    target.focus()
                except Exception as exc:
                    log.tui.warning(
                        "[tui] overlay_panel.close: focus restore failed",
                        exc_info=exc,
                        extra={"_fields": {"overlay_name": self.overlay_name}},
                    )

    def on_key(self, event: events.Key) -> None:
        """Intercept Escape so the overlay closes before key bubbles."""
        if event.key == "escape":
            event.prevent_default()
            self.close()

    def action_close_overlay(self) -> None:
        """Binding action target — delegates to :meth:`close`."""
        self.close()


class OverlayQueue:
    """One-at-a-time sequencer for :class:`OverlayPanel` instances."""

    def __init__(self) -> None:
        log.tui.debug(
            "[tui] overlay_queue.__init__: entry",
            extra={"_fields": {}},
        )
        self._queue: deque[OverlayPanel] = deque()
        self._active: OverlayPanel | None = None

    @property
    def active(self) -> OverlayPanel | None:
        return self._active

    @property
    def pending(self) -> int:
        return len(self._queue)

    def push(self, panel: OverlayPanel) -> None:
        """Queue an overlay; open immediately when nothing is active."""
        log.tui.debug(
            "[tui] overlay_queue.push: entry",
            extra={
                "_fields": {
                    "overlay_name": panel.overlay_name,
                    "active": self._active.overlay_name if self._active else None,
                    "pending": len(self._queue),
                }
            },
        )
        if self._active is None:
            self._active = panel
            try:
                panel.open_overlay()
            except Exception as exc:
                log.tui.warning(
                    "[tui] overlay_queue.push: open_overlay failed",
                    exc_info=exc,
                    extra={"_fields": {"overlay_name": panel.overlay_name}},
                )
                self._active = None
        else:
            self._queue.append(panel)

    def on_closed(self, panel: OverlayPanel) -> None:
        """Advance the queue when ``panel`` (the active overlay) closes."""
        log.tui.debug(
            "[tui] overlay_queue.on_closed: entry",
            extra={
                "_fields": {
                    "overlay_name": panel.overlay_name,
                    "pending": len(self._queue),
                }
            },
        )
        if self._active is panel:
            self._active = None
        if self._queue:
            nxt = self._queue.popleft()
            self._active = nxt
            try:
                nxt.open_overlay()
            except Exception as exc:
                log.tui.warning(
                    "[tui] overlay_queue.on_closed: open_overlay failed",
                    exc_info=exc,
                    extra={"_fields": {"overlay_name": nxt.overlay_name}},
                )
                self._active = None
