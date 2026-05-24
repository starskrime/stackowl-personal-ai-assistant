"""ConversationView — streaming chat transcript with citations and auto-scroll."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widget import Widget
from textual.widgets import RichLog

from stackowl.infra.observability import log
from stackowl.tui.glyphs import GLYPH_SEPARATOR
from stackowl.tui.messages import ResponseChunkMessage
from stackowl.tui.widgets.conversation_helpers import (
    DEFAULT_FLUSH_INTERVAL_SEC,
    ChunkRenderer,
    ScrollState,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from textual.app import ComposeResult

_LOG_ID = "conversation_log"


class ConversationView(Widget):
    """Chat transcript with streaming support, citations, and auto-scroll.

    Incoming :class:`ResponseChunkMessage` events are queued in
    ``_pending_chunks`` and flushed on a fixed interval (``DEFAULT_FLUSH_INTERVAL_SEC``)
    so a burst of streaming tokens never overwhelms the terminal render loop.

    When the user scrolls away from the bottom, auto-scroll is suspended and
    a transient "new messages" hint is rendered.  Pressing ``End`` resumes
    auto-scroll.
    """

    DEFAULT_CSS = """
    ConversationView {
        layer: base;
        height: 1fr;
        border: solid $color-border;
        background: $color-bg;
    }
    ConversationView RichLog {
        background: $color-bg;
        color: $color-text-primary;
    }
    """

    BINDINGS = [
        ("up", "scroll_up", "Scroll up"),
        ("down", "scroll_down", "Scroll down"),
        ("pageup", "scroll_page_up", "Page up"),
        ("pagedown", "scroll_page_down", "Page down"),
        ("home", "scroll_home", "Scroll to top"),
        ("end", "scroll_end", "Scroll to bottom"),
    ]

    def __init__(self, *, flush_interval_sec: float = DEFAULT_FLUSH_INTERVAL_SEC) -> None:
        super().__init__()
        log.tui.debug(
            "[tui] conversation_view.__init__: entry",
            extra={"_fields": {"flush_interval_sec": flush_interval_sec}},
        )
        self._auto_scroll: bool = True
        self._pending_chunks: list[ResponseChunkMessage] = []
        self._flush_interval_sec: float = flush_interval_sec
        self._renderer: ChunkRenderer = ChunkRenderer(
            separator_glyph=str(GLYPH_SEPARATOR)
        )
        self._scroll_state: ScrollState = ScrollState()
        self._pending_hint_shown: bool = False

    # ------------------------------------------------------------------ lifecycle

    def compose(self) -> ComposeResult:
        yield RichLog(
            highlight=True,
            markup=True,
            wrap=True,
            auto_scroll=False,  # we drive scroll explicitly to honour user override
            id=_LOG_ID,
        )

    def on_mount(self) -> None:
        log.tui.debug(
            "[tui] conversation_view.on_mount: entry",
            extra={"_fields": {"flush_interval_sec": self._flush_interval_sec}},
        )
        self.set_interval(self._flush_interval_sec, self._flush_pending)

    # ------------------------------------------------------------------ messages

    def on_response_chunk_message(self, message: ResponseChunkMessage) -> None:
        """Queue chunk for debounced write."""
        log.tui.debug(
            "[tui] conversation_view.on_response_chunk_message: entry",
            extra={
                "_fields": {
                    "owl_name": message.owl_name,
                    "chunk_index": message.chunk_index,
                    "text_len": len(message.text),
                    "citations": len(message.citations),
                    "trace_id": message.trace_id,
                }
            },
        )
        self._pending_chunks.append(message)

    # ------------------------------------------------------------------ flush

    def _flush_pending(self) -> None:
        """Write queued chunks to RichLog (debounced)."""
        if not self._pending_chunks:
            return
        try:
            log_widget = self.query_one(f"#{_LOG_ID}", RichLog)
        except Exception as exc:  # widget not mounted yet (tests)
            log.tui.warning(
                "[tui] conversation_view._flush_pending: log widget unavailable",
                exc_info=exc,
                extra={"_fields": {"pending": len(self._pending_chunks)}},
            )
            return

        chunks: list[ResponseChunkMessage] = list(self._pending_chunks)
        self._pending_chunks.clear()
        log.tui.debug(
            "[tui] conversation_view._flush_pending: decision write_batch",
            extra={"_fields": {"count": len(chunks)}},
        )
        for chunk in chunks:
            rendered = self._render_chunk(chunk)
            log_widget.write(rendered)
        self._check_auto_scroll(log_widget)
        if self._auto_scroll:
            log_widget.scroll_end(animate=False)
        log.tui.debug(
            "[tui] conversation_view._flush_pending: exit",
            extra={
                "_fields": {
                    "written": len(chunks),
                    "auto_scroll": self._auto_scroll,
                }
            },
        )

    # ------------------------------------------------------------------ render

    def _render_chunk(self, chunk: ResponseChunkMessage) -> str:
        """Format chunk text with markup for citations, pushback, synthesis."""
        return self._renderer.render(chunk)

    def _render_chunks(self, chunks: Iterable[ResponseChunkMessage]) -> list[str]:
        return [self._renderer.render(c) for c in chunks]

    # ------------------------------------------------------------------ scroll

    def _check_auto_scroll(self, log_widget: RichLog) -> None:
        """Update :attr:`_auto_scroll` based on the user's current scroll position.

        If the user has scrolled away from the bottom, suspend auto-scroll and
        emit a one-shot debug hint.  Resumption happens via :meth:`action_scroll_end`.
        """
        at_bottom = self._scroll_state.is_at_bottom(log_widget)
        if not at_bottom and self._auto_scroll:
            self._auto_scroll = False
            self._pending_hint_shown = True
            log.tui.debug(
                "[tui] conversation_view._check_auto_scroll: suspended",
                extra={"_fields": {"reason": "user_scrolled_up"}},
            )
        elif at_bottom and not self._auto_scroll:
            self._auto_scroll = True
            self._pending_hint_shown = False
            log.tui.debug(
                "[tui] conversation_view._check_auto_scroll: resumed",
                extra={"_fields": {"reason": "back_at_bottom"}},
            )

    # ------------------------------------------------------------------ actions

    def action_scroll_end(self) -> None:
        """Resume auto-scroll and jump to the bottom of the transcript."""
        log.tui.debug(
            "[tui] conversation_view.action_scroll_end: entry",
            extra={"_fields": {}},
        )
        self._auto_scroll = True
        self._pending_hint_shown = False
        try:
            log_widget = self.query_one(f"#{_LOG_ID}", RichLog)
        except Exception as exc:
            log.tui.warning(
                "[tui] conversation_view.action_scroll_end: log widget unavailable",
                exc_info=exc,
                extra={"_fields": {}},
            )
            return
        log_widget.scroll_end(animate=False)

    def action_scroll_home(self) -> None:
        log.tui.debug(
            "[tui] conversation_view.action_scroll_home: entry",
            extra={"_fields": {}},
        )
        self._auto_scroll = False
        try:
            log_widget = self.query_one(f"#{_LOG_ID}", RichLog)
        except Exception as exc:
            log.tui.warning(
                "[tui] conversation_view.action_scroll_home: log widget unavailable",
                exc_info=exc,
                extra={"_fields": {}},
            )
            return
        log_widget.scroll_home(animate=False)

    def action_scroll_up(self) -> None:
        self._delegate_scroll("scroll_up")

    def action_scroll_down(self) -> None:
        self._delegate_scroll("scroll_down")

    def action_scroll_page_up(self) -> None:
        self._delegate_scroll("scroll_page_up")

    def action_scroll_page_down(self) -> None:
        self._delegate_scroll("scroll_page_down")

    def _delegate_scroll(self, method_name: str) -> None:
        log.tui.debug(
            "[tui] conversation_view._delegate_scroll: entry",
            extra={"_fields": {"method": method_name}},
        )
        try:
            log_widget = self.query_one(f"#{_LOG_ID}", RichLog)
        except Exception as exc:
            log.tui.warning(
                "[tui] conversation_view._delegate_scroll: log widget unavailable",
                exc_info=exc,
                extra={"_fields": {"method": method_name}},
            )
            return
        method = getattr(log_widget, method_name, None)
        if method is None:
            log.tui.warning(
                "[tui] conversation_view._delegate_scroll: unknown method",
                extra={"_fields": {"method": method_name}},
            )
            return
        # Any manual scrolling disables auto-scroll until user presses End.
        self._auto_scroll = False
        method()
