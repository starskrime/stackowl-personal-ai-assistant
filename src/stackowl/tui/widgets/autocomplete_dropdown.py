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

Rows are :class:`DropdownRow` ``(name, description, kind)``.  AI-augmented rows
(``suggested``/``semantic``) render with a dim ``☆`` mark and are NEVER the default
highlight (honesty spine): the highlight initialises to the first deterministic
row.  A semantic panel may open with NO selection at all (``allow_no_selection``)
so Enter submits the prose instead of hijacking it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from stackowl.infra.observability import log
from stackowl.tui.widgets.compose_helpers import (
    _STAR_KINDS,
    ROW_SEMANTIC,
    ROW_SUGGESTED,
    DropdownRow,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rich.console import RenderableType

_STAR_HEADERS = {
    ROW_SUGGESTED: "☆ suggested — you usually do this next",
    ROW_SEMANTIC: "☆ matches for what you typed — Tab to use, never runs on its own",
}


class AutocompleteDropdown(Static):
    """An overlay list of completion candidates with a navigable highlight.

    Items are :class:`DropdownRow` records; ``description`` is ``None`` for owl
    mentions (name only) and the command one-liner for slash commands.  The
    highlighted index is a clamped reactive — :meth:`move_up` / :meth:`move_down`
    never wrap (top/bottom are sticky), which keeps the keyboard model obvious.
    A highlight of ``-1`` means "no selection" (only when ``allow_no_selection``).
    """

    # Visible row budget. The dropdown is a Static (no native scroll), so when
    # there are more candidates than this we render a sliding window that keeps
    # the highlighted row on screen — see :meth:`visible_range` / :meth:`render`.
    # The CSS ``max-height`` must fit this window plus the two ``N more`` hint
    # rows / the ``☆`` header and the round border: 9 items + 2 + 2 border = 13.
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
        self._items: list[DropdownRow] = []
        # Index of the first row currently shown — the scroll offset that keeps
        # the highlight inside the visible window (move_up/move_down adjust it).
        self._offset: int = 0
        # When True the list may rest with NO row selected (highlight == -1) so
        # Enter falls through to submit — used by the semantic (prose) panel.
        self._allow_no_selection: bool = False

    # ------------------------------------------------------------------ data
    @staticmethod
    def _normalize(
        items: Sequence[DropdownRow | tuple[str, str | None]],
    ) -> list[DropdownRow]:
        """Accept legacy ``(name, description)`` tuples OR tagged DropdownRows."""
        out: list[DropdownRow] = []
        for it in items:
            if isinstance(it, DropdownRow):
                out.append(it)
            else:
                name, description = it
                out.append(DropdownRow(name=name, description=description))
        return out

    def set_items(
        self,
        items: Sequence[DropdownRow | tuple[str, str | None]],
        *,
        allow_no_selection: bool = False,
    ) -> None:
        """Replace the visible candidate list and reset the highlight.

        The highlight resets to the first DETERMINISTIC row (AI rows are never
        the default), or to ``-1`` (no selection) when ``allow_no_selection`` —
        the semantic panel uses that so Enter submits the prose.
        """
        self._items = self._normalize(items)
        self._allow_no_selection = allow_no_selection
        self._offset = 0
        self.highlight = -1 if allow_no_selection else self._first_selectable_index()
        self.refresh(layout=True)
        log.tui.debug(
            "[tui] autocomplete_dropdown.set_items: exit",
            extra={"_fields": {
                "count": len(self._items), "highlight": self.highlight,
                "no_select": allow_no_selection,
            }},
        )

    def _first_selectable_index(self) -> int:
        """First deterministic (non-AI) row, so an AI row is never default."""
        for i, row in enumerate(self._items):
            if row.kind not in _STAR_KINDS:
                return i
        return 0

    # ------------------------------------------------------------------ access
    @property
    def count(self) -> int:
        """Number of items currently displayed."""
        return len(self._items)

    @property
    def is_empty(self) -> bool:
        """``True`` when there is nothing to complete."""
        return not self._items

    def _clamped_index(self) -> int | None:
        """The currently selected index, or ``None`` when nothing is selected."""
        if not self._items or self.highlight < 0:
            return None
        return max(0, min(self.highlight, len(self._items) - 1))

    def current(self) -> str | None:
        """Return the highlighted candidate's name, or ``None`` when none."""
        idx = self._clamped_index()
        if idx is None:
            return None
        return self._items[idx].name

    def current_row(self) -> DropdownRow | None:
        """Return the highlighted :class:`DropdownRow`, or ``None`` when none."""
        idx = self._clamped_index()
        return None if idx is None else self._items[idx]

    # ------------------------------------------------------------------ nav
    def move_down(self) -> None:
        """Move the highlight down one row (clamped, no wrap). From none → first."""
        if not self._items:
            return
        if self.highlight < 0:
            self.highlight = 0
        else:
            self.highlight = min(self.highlight + 1, len(self._items) - 1)
        self._scroll_into_view()

    def move_up(self) -> None:
        """Move the highlight up one row. From the top → none when allowed."""
        if not self._items:
            return
        if self.highlight <= 0:
            self.highlight = -1 if self._allow_no_selection else 0
        else:
            self.highlight = self.highlight - 1
        self._scroll_into_view()

    def _scroll_into_view(self) -> None:
        """Nudge the scroll offset so the highlighted row stays on screen."""
        if self.highlight < 0:
            self._offset = 0
            return
        if self.highlight < self._offset:
            self._offset = self.highlight
        elif self.highlight >= self._offset + self._VISIBLE_ROWS:
            self._offset = self.highlight - self._VISIBLE_ROWS + 1

    def visible_range(self) -> tuple[int, int]:
        """``(start, end)`` half-open index range currently rendered."""
        start = max(0, min(self._offset, max(0, len(self._items) - self._VISIBLE_ROWS)))
        end = min(start + self._VISIBLE_ROWS, len(self._items))
        return (start, end)

    # ------------------------------------------------------------------ render
    def watch_highlight(self, _old: int, _new: int) -> None:
        """Re-render when the highlight changes so the marker tracks the row."""
        self.refresh(layout=True)

    def render(self) -> RenderableType:
        """Render the visible candidate window.

        The highlighted row gets a ``▶ `` accent; AI rows (``suggested``/
        ``semantic``) get a dim ``☆`` mark and a one-line header above their
        block (shown only at the top of the window, where no ``↑ N more`` hint
        competes for the line).  A ``-1`` highlight draws no marker at all.
        """
        if not self._items:
            return Text("")
        idx = self._clamped_index()  # None when no selection
        start, end = self.visible_range()
        rows: list[Text] = []
        # Header for an AI block at the very top (mutually exclusive with the
        # "↑ N more" hint, which only appears when start > 0).
        if start == 0 and self._items[0].kind in _STAR_KINDS:
            header = _STAR_HEADERS.get(self._items[0].kind, "☆ suggested")
            rows.append(Text(f"  {header}", style="dim italic"))
        elif start > 0:
            rows.append(Text(f"  ↑ {start} more", style="dim"))
        for i in range(start, end):
            row = self._items[i]
            selected = idx is not None and i == idx
            is_star = row.kind in _STAR_KINDS
            marker = "▶ " if selected else ("☆ " if is_star else "  ")
            if selected:
                row_style = "bold reverse"
            elif is_star:
                row_style = "dim"
            else:
                row_style = ""
            line = Text(marker, style=row_style)
            line.append(row.name, style=row_style)
            if row.description:
                line.append(" — ", style="dim")
                line.append(row.description, style="dim")
            rows.append(line)
        hidden_below = len(self._items) - end
        if hidden_below > 0:
            rows.append(Text(f"  ↓ {hidden_below} more", style="dim"))
        return Group(*rows)
