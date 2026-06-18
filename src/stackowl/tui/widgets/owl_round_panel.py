"""OwlRoundPanel — single owl's response pane inside a Parliament round."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widget import Widget
from textual.widgets import RichLog

from stackowl.infra.observability import log

if TYPE_CHECKING:
    from textual.app import ComposeResult


_OWL_LOG_ID_PREFIX = "owl_panel_"
_COLLAPSE_LINES = 2


class OwlRoundPanel(Widget):
    """One owl's response pane within a Parliament round."""

    DEFAULT_CSS = """
    OwlRoundPanel {
        height: auto;
        background: $color-surface;
        border: solid $color-border;
        color: $color-text-primary;
    }
    OwlRoundPanel.collapsed {
        max-height: 3;
    }
    """

    def __init__(self, owl_name: str) -> None:
        super().__init__()
        log.tui.debug(
            "[tui] owl_round_panel.__init__: entry",
            extra={"_fields": {"owl_name": owl_name}},
        )
        self._owl_name: str = owl_name
        self._buffer: list[str] = []
        self._collapsed: bool = False

    @property
    def owl_name(self) -> str:
        return self._owl_name

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    @property
    def collapse_threshold(self) -> int:
        """Maximum visible lines while collapsed — used by assertions/CSS docs."""
        return _COLLAPSE_LINES

    def compose(self) -> ComposeResult:
        yield RichLog(
            highlight=False,
            markup=False,
            wrap=True,
            auto_scroll=False,
            id=f"{_OWL_LOG_ID_PREFIX}{self._owl_name}",
        )

    def append_text(self, text: str) -> None:
        """Append ``text`` to the owl's RichLog (no markup, no highlighting)."""
        log.tui.debug(
            "[tui] owl_round_panel.append_text: entry",
            extra={"_fields": {"owl_name": self._owl_name, "len": len(text)}},
        )
        self._buffer.append(text)
        try:
            log_widget = self.query_one(
                f"#{_OWL_LOG_ID_PREFIX}{self._owl_name}", RichLog
            )
        except Exception as exc:
            log.tui.warning(
                "[tui] owl_round_panel.append_text: RichLog unavailable",
                exc_info=exc,
                extra={"_fields": {"owl_name": self._owl_name}},
            )
            return
        log_widget.write(text)

    def collapse(self) -> None:
        """Shrink the panel by toggling the ``collapsed`` CSS class."""
        log.tui.debug(
            "[tui] owl_round_panel.collapse: entry",
            extra={"_fields": {"owl_name": self._owl_name}},
        )
        self._collapsed = True
        try:
            self.add_class("collapsed")
        except Exception as exc:
            log.tui.warning(
                "[tui] owl_round_panel.collapse: add_class failed",
                exc_info=exc,
                extra={"_fields": {"owl_name": self._owl_name}},
            )

    def uncollapse(self) -> None:
        """Restore full height by removing the ``collapsed`` class.

        Named ``uncollapse`` (not ``expand``) because ``Widget.expand`` is a
        reactive bool on the base class — overriding it with a method would
        violate the Liskov substitution principle.
        """
        log.tui.debug(
            "[tui] owl_round_panel.uncollapse: entry",
            extra={"_fields": {"owl_name": self._owl_name}},
        )
        self._collapsed = False
        try:
            self.remove_class("collapsed")
        except Exception as exc:
            log.tui.warning(
                "[tui] owl_round_panel.uncollapse: remove_class failed",
                exc_info=exc,
                extra={"_fields": {"owl_name": self._owl_name}},
            )
