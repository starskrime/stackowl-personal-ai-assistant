"""AutocompleteDropdown — overlay candidate list for slash / @-mention completion.

A small, self-contained overlay rendered as a single :class:`~textual.widgets.Static`
(the same pattern as :class:`MessageBubble`) so it auto-sizes to its content and —
crucially — never steals focus from the compose editor: the user keeps typing while
the dropdown filters in place.  A ``ListView`` would grab focus and break that
ergonomic, so we re-render a Rich table on every index/items change instead.

The widget is positioned ABOVE the compose box (which docks at the screen bottom)
via ``dock: bottom`` + a negative ``offset-y`` so it floats over the conversation
on the ``top`` layer.  Highlighting is a ``▶ `` prefix plus an accent style on the
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

    DEFAULT_CSS = """
    AutocompleteDropdown {
        layer: top;
        dock: bottom;
        offset-y: -6;
        width: auto;
        max-width: 60;
        height: auto;
        max-height: 8;
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

    # ------------------------------------------------------------------ data
    def set_items(self, items: list[tuple[str, str | None]]) -> None:
        """Replace the visible candidate list and reset the highlight to the top."""
        log.tui.debug(
            "[tui] autocomplete_dropdown.set_items: entry",
            extra={"_fields": {"count": len(items)}},
        )
        self._items = list(items)
        # Reset highlight; assigning the same value (0 == 0) still needs an
        # explicit re-render, so refresh unconditionally below.
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

    # ------------------------------------------------------------------ render
    def watch_highlight(self, _old: int, _new: int) -> None:
        """Re-render when the highlight changes so the marker tracks the row."""
        self.refresh(layout=True)

    def render(self) -> RenderableType:
        """Render the candidate rows; the highlighted row gets a ``▶ `` accent."""
        if not self._items:
            return Text("")
        idx = max(0, min(self.highlight, len(self._items) - 1))
        rows: list[Text] = []
        for i, (name, description) in enumerate(self._items):
            selected = i == idx
            marker = "▶ " if selected else "  "
            row_style = "bold reverse" if selected else ""
            line = Text(marker, style=row_style)
            line.append(name, style=row_style)
            if description:
                line.append(" — ", style="dim")
                line.append(description, style="dim")
            rows.append(line)
        return Group(*rows)
