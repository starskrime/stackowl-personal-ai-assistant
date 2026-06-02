"""ConversationView — streaming chat transcript of mounted bubbles + auto-scroll."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import VerticalScroll
from textual.widget import Widget

from stackowl.infra.observability import log
from stackowl.tui.glyphs import GLYPH_SEPARATOR
from stackowl.tui.messages import ResponseChunkMessage, UserTurnMessage
from stackowl.tui.widgets.conversation_helpers import (
    DEFAULT_FLUSH_INTERVAL_SEC,
    ChunkRenderer,
    ScrollState,
)
from stackowl.tui.widgets.message_bubble import MessageBubble, MessageRow

if TYPE_CHECKING:
    from collections.abc import Iterable

    from textual.app import ComposeResult

_TRANSCRIPT_ID = "transcript"


class ConversationView(Widget):
    """Chat transcript built from mounted bubbles, with streaming + auto-scroll.

    Incoming :class:`ResponseChunkMessage` events are queued in
    ``_pending_chunks`` and flushed on a fixed interval (``DEFAULT_FLUSH_INTERVAL_SEC``)
    so a burst of streaming tokens never overwhelms the terminal render loop.
    Each flush appends into the *active* agent bubble; a fresh bubble opens when
    the trace id changes (turn end) or a synthesis chunk arrives.

    When the user scrolls away from the bottom, auto-scroll is suspended.
    Pressing ``End`` resumes auto-scroll.
    """

    DEFAULT_CSS = """
    ConversationView {
        height: 1fr;
        border: solid $color-border;
        background: $color-bg;
    }
    ConversationView VerticalScroll {
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
        # The agent bubble currently being streamed into, plus the trace id that
        # owns it. A change of trace id (or a synthesis chunk) opens a new bubble.
        self._active_bubble: MessageBubble | None = None
        self._active_trace_id: str | None = None

    # ------------------------------------------------------------------ lifecycle

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id=_TRANSCRIPT_ID)

    def on_mount(self) -> None:
        log.tui.debug(
            "[tui] conversation_view.on_mount: entry",
            extra={"_fields": {"flush_interval_sec": self._flush_interval_sec}},
        )
        self.set_interval(self._flush_interval_sec, self._flush_pending)

    # ------------------------------------------------------------------ helpers

    def _container(self) -> VerticalScroll | None:
        """Return the scrollable transcript container (self-healing)."""
        try:
            return self.query_one(f"#{_TRANSCRIPT_ID}", VerticalScroll)
        except Exception as exc:  # not mounted yet (tests / teardown)
            log.tui.warning(
                "[tui] conversation_view._container: transcript unavailable",
                exc_info=exc,
                extra={"_fields": {}},
            )
            return None

    # ------------------------------------------------------------------ messages

    def on_user_turn_message(self, message: UserTurnMessage) -> None:
        """Mount the user's own submitted turn as a right-aligned bubble.

        The bubble body is rendered NON-markup so arbitrary user input —
        including ``[`` — is shown verbatim and cannot inject Rich markup.
        """
        log.tui.debug(
            "[tui] conversation_view.on_user_turn_message: entry",
            extra={"_fields": {"text_len": len(message.text)}},
        )
        # The user is speaking — any active agent bubble is now closed.
        self._active_bubble = None
        self._active_trace_id = None
        container = self._container()
        if container is None:
            log.tui.warning(
                "[tui] conversation_view.on_user_turn_message: container unavailable",
                extra={"_fields": {"text_len": len(message.text)}},
            )
            return
        bubble = MessageBubble(role="user", text=message.text)
        row = MessageRow(bubble, role="user")
        container.mount(row)
        if self._auto_scroll:
            container.scroll_end(animate=False)
        log.tui.debug(
            "[tui] conversation_view.on_user_turn_message: exit",
            extra={"_fields": {"text_len": len(message.text)}},
        )

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
        """Append queued chunks into the active agent bubble (debounced)."""
        if not self._pending_chunks:
            return
        container = self._container()
        if container is None:
            # Widget unavailable — leave chunks queued; the next tick retries.
            return

        chunks: list[ResponseChunkMessage] = list(self._pending_chunks)
        self._pending_chunks.clear()
        log.tui.debug(
            "[tui] conversation_view._flush_pending: decision write_batch",
            extra={"_fields": {"count": len(chunks)}},
        )
        opened = 0
        for chunk in chunks:
            # An empty terminal marker with nothing open closes nothing and must
            # not spawn a stray empty bubble.
            if chunk.is_final and not chunk.text and self._active_bubble is None:
                continue
            new_turn = (
                self._active_bubble is None
                or chunk.trace_id != self._active_trace_id
                or chunk.is_synthesis
            )
            if new_turn:
                bubble = MessageBubble(role="agent", owl_name=chunk.owl_name)
                self._active_bubble = bubble
                self._active_trace_id = chunk.trace_id
                container.mount(MessageRow(bubble, role="agent"))
                opened += 1
            self._active_bubble.append(self._renderer.render(chunk))
            # A final chunk closes the turn so the next chunk opens a fresh
            # bubble even if its trace id were to repeat.
            if chunk.is_final:
                self._active_bubble = None
                self._active_trace_id = None
        self._check_auto_scroll(container)
        if self._auto_scroll:
            container.scroll_end(animate=False)
        log.tui.debug(
            "[tui] conversation_view._flush_pending: exit",
            extra={
                "_fields": {
                    "written": len(chunks),
                    "bubbles_opened": opened,
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

    def _check_auto_scroll(self, container: VerticalScroll) -> None:
        """Update :attr:`_auto_scroll` based on the user's scroll position.

        If the user has scrolled away from the bottom, suspend auto-scroll.
        Resumption happens via :meth:`action_scroll_end`.
        """
        at_bottom = self._scroll_state.is_at_bottom(container)
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
        container = self._container()
        if container is None:
            return
        container.scroll_end(animate=False)

    def action_scroll_home(self) -> None:
        log.tui.debug(
            "[tui] conversation_view.action_scroll_home: entry",
            extra={"_fields": {}},
        )
        self._auto_scroll = False
        container = self._container()
        if container is None:
            return
        container.scroll_home(animate=False)

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
        container = self._container()
        if container is None:
            return
        method = getattr(container, method_name, None)
        if method is None:
            log.tui.warning(
                "[tui] conversation_view._delegate_scroll: unknown method",
                extra={"_fields": {"method": method_name}},
            )
            return
        # Any manual scrolling disables auto-scroll until user presses End.
        self._auto_scroll = False
        method()
