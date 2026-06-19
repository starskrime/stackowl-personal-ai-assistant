"""AutocompleteDropdown — overlay candidate list for slash / @-mention completion.

A small, self-contained overlay rendered as a single :class:`~textual.widgets.Static`
(the same pattern as :class:`MessageBubble`) so it auto-sizes to its content and —
crucially — never steals focus from the compose editor: the user keeps typing while
the dropdown filters in place.  A ``ListView`` would grab focus and break that
ergonomic, so we re-render a Rich table on every index/items change instead.

The widget lives IN-FLOW as the top child of the (auto-height) compose zone, so it
renders directly above the input and is never clipped — an earlier version docked it
with a negative offset on the ``top`` layer, which placed it outside the compose
region so the parent clipped it (invisible).  Highlighting is a ``▶ `` prefix plus an accent style on the
selected row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from stackowl.infra.observability import log

if TYPE_CHECKING:
    from rich.console import RenderableType


class AutocompleteDropdown(Static):
    """An overlay list of completion candidates with a navigable highlight.

    Items are ``(name, description)`` pairs; ``description`` is ``None`` for owl
    mentions (name only) and the command one-liner for slash commands.  The
    highlighted index is a clamped reactive — :meth:`move_up` / :meth:`move_down`
    never wrap (top/bottom are sticky), which keeps the keyboard model obvious.
    """

    # Visible row budget. The dropdown is a Static (no native scroll), so when
    # there are more candidates than this we render a sliding window that keeps
    # the highlighted row on screen — see :meth:`visible_range` / :meth:`render`.
    # The CSS ``max-height`` must fit this window plus the two ``N more`` hint
    # rows and the round border: 9 items + 2 hints + 2 border = 13.
    _VISIBLE_ROWS = 9

    DEFAULT_CSS = """
    AutocompleteDropdown {
        width: 100%;
        height: auto;
        max-height: 13;
        border: round $color-accent;
        background: $color-bg-elevated;
        padding: 0 1;
    }
    """

    highlight: reactive[int] = reactive(0)

    def __init__(self, *, id: str | None = None) -> None:  # noqa: A002 - Textual API
        super().__init__(id=id)
        log.tui.debug(
            "[tui] autocomplete_dropdown.__init__: entry",
            extra={"_fields": {"id": id}},
        )
        self._items: list[tuple[str, str | None]] = []
        # Index of the first row currently shown — the scroll offset that keeps
        # the highlight inside the visible window (move_up/move_down adjust it).
        self._offset: int = 0

    # ------------------------------------------------------------------ data
    def set_items(self, items: list[tuple[str, str | None]]) -> None:
        """Replace the visible candidate list and reset the highlight to the top."""
        log.tui.debug(
            "[tui] autocomplete_dropdown.set_items: entry",
            extra={"_fields": {"count": len(items)}},
        )
        self._items = list(items)
        # Reset highlight + scroll window; assigning the same value (0 == 0)
        # still needs an explicit re-render, so refresh unconditionally below.
        self._offset = 0
        self.highlight = 0
        self.refresh(layout=True)
        log.tui.debug(
            "[tui] autocomplete_dropdown.set_items: exit",
            extra={"_fields": {"count": len(self._items), "highlight": self.highlight}},
        )

    # ------------------------------------------------------------------ access
    @property
    def count(self) -> int:
        """Number of items currently displayed."""
        return len(self._items)

    @property
    def is_empty(self) -> bool:
        """``True`` when there is nothing to complete."""
        return not self._items

    def current(self) -> str | None:
        """Return the highlighted candidate's name, or ``None`` when empty."""
        if not self._items:
            log.tui.debug(
                "[tui] autocomplete_dropdown.current: empty",
                extra={"_fields": {}},
            )
            return None
        idx = max(0, min(self.highlight, len(self._items) - 1))
        name = self._items[idx][0]
        log.tui.debug(
            "[tui] autocomplete_dropdown.current: exit",
            extra={"_fields": {"index": idx, "name": name}},
        )
        return name

    # ------------------------------------------------------------------ nav
    def move_down(self) -> None:
        """Move the highlight down one row (clamped, no wrap)."""
        if not self._items:
            return
        new_index = min(self.highlight + 1, len(self._items) - 1)
        log.tui.debug(
            "[tui] autocomplete_dropdown.move_down: decision",
            extra={"_fields": {"from": self.highlight, "to": new_index}},
        )
        self.highlight = new_index
        self._scroll_into_view()

    def move_up(self) -> None:
        """Move the highlight up one row (clamped at the top, no wrap)."""
        if not self._items:
            return
        new_index = max(self.highlight - 1, 0)
        log.tui.debug(
            "[tui] autocomplete_dropdown.move_up: decision",
            extra={"_fields": {"from": self.highlight, "to": new_index}},
        )
        self.highlight = new_index
        self._scroll_into_view()

    def _scroll_into_view(self) -> None:
        """Nudge the scroll offset so the highlighted row stays on screen."""
        if self.highlight < self._offset:
            self._offset = self.highlight
        elif self.highlight >= self._offset + self._VISIBLE_ROWS:
            self._offset = self.highlight - self._VISIBLE_ROWS + 1

    def visible_range(self) -> tuple[int, int]:
        """``(start, end)`` half-open index range currently rendered.

        ``end`` is exclusive and clamped to the item count.  The highlighted
        index is always within ``[start, end)`` — the scroll-window invariant.
        """
        start = max(0, min(self._offset, max(0, len(self._items) - self._VISIBLE_ROWS)))
        end = min(start + self._VISIBLE_ROWS, len(self._items))
        return (start, end)

    # ------------------------------------------------------------------ render
    def watch_highlight(self, _old: int, _new: int) -> None:
        """Re-render when the highlight changes so the marker tracks the row."""
        self.refresh(layout=True)

    def render(self) -> RenderableType:
        """Render the visible candidate window; highlighted row gets a ``▶ `` accent.

        Only the ``visible_range`` slice is drawn so a list larger than
        :attr:`_VISIBLE_ROWS` (e.g. all ~29 slash commands) scrolls with the
        highlight rather than being clipped.  A dim ``↑``/``↓`` counter is shown
        on the first/last row when rows are hidden above/below.
        """
        if not self._items:
            return Text("")
        idx = max(0, min(self.highlight, len(self._items) - 1))
        start, end = self.visible_range()
        rows: list[Text] = []
        if start > 0:
            rows.append(Text(f"  ↑ {start} more", style="dim"))
        for i in range(start, end):
            name, description = self._items[i]
            selected = i == idx
            marker = "▶ " if selected else "  "
            row_style = "bold reverse" if selected else ""
            line = Text(marker, style=row_style)
            line.append(name, style=row_style)
            if description:
                line.append(" — ", style="dim")
                line.append(description, style="dim")
            rows.append(line)
        hidden_below = len(self._items) - end
        if hidden_below > 0:
            rows.append(Text(f"  ↓ {hidden_below} more", style="dim"))
        return Group(*rows)
