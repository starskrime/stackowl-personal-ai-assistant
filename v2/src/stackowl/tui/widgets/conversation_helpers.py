"""Helpers for ConversationView — chunk rendering and scroll-position tracking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.tui.messages import ResponseChunkMessage

if TYPE_CHECKING:
    from textual.widgets import RichLog

#: Default debounce interval (~60fps) for flushing queued chunks.
DEFAULT_FLUSH_INTERVAL_SEC: float = 0.016

#: Inline marker rendered to the left of a parliament pushback chunk.
PUSHBACK_INDICATOR: str = "◀"  # BLACK LEFT-POINTING TRIANGLE


@dataclass(frozen=True)
class ChunkRenderer:
    """Formats :class:`ResponseChunkMessage` instances into RichLog markup.

    The renderer is split out of the widget so the formatting logic can be
    unit-tested without spinning up a Textual app.
    """

    separator_glyph: str

    def render(self, chunk: ResponseChunkMessage) -> str:
        """Return a markup-ready string for the given chunk."""
        text = chunk.text
        if chunk.is_synthesis:
            return f"[{self.separator_glyph}]\n{text}"
        if chunk.is_pushback:
            return f"{PUSHBACK_INDICATOR} {text}"
        if chunk.citations:
            return text + "".join(f" [{c.index}]" for c in chunk.citations)
        return text


class ScrollState:
    """Tracks whether a :class:`textual.widgets.RichLog` is pinned at the bottom.

    Wrapping the check in a small object lets the widget hand a fake to tests
    that doesn't require a full Textual layout pass.
    """

    def is_at_bottom(self, log_widget: RichLog) -> bool:
        """Return ``True`` when the user is viewing the tail of the log."""
        try:
            return bool(getattr(log_widget, "is_vertical_scroll_end", True))
        except Exception as exc:
            log.tui.warning(
                "[tui] scroll_state.is_at_bottom: probe failed",
                exc_info=exc,
                extra={"_fields": {"widget": type(log_widget).__name__}},
            )
            return True
