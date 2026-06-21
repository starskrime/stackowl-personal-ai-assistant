"""Unit tests for AutocompleteDropdown row-kind + no-selection behaviour."""

from __future__ import annotations

import pytest

from stackowl.tui.widgets.autocomplete_dropdown import AutocompleteDropdown
from stackowl.tui.widgets.compose_helpers import (
    ROW_SEMANTIC,
    ROW_SUGGESTED,
    DropdownRow,
)

pytestmark = pytest.mark.tui


def test_legacy_tuples_still_accepted() -> None:
    dd = AutocompleteDropdown()
    dd.set_items([("memory", "manage memory"), ("audit", "audit log")])
    assert dd.count == 2
    assert dd.current() == "memory"
    # legacy index access preserved
    assert dd._items[0][0] == "memory"


def test_suggested_rows_are_never_default_highlight() -> None:
    dd = AutocompleteDropdown()
    dd.set_items(
        [
            DropdownRow("/memory remember", "you usually do this next", ROW_SUGGESTED),
            DropdownRow("/parliament", "you usually do this next", ROW_SUGGESTED),
            ("memory", "manage memory"),
            ("audit", "audit log"),
        ]
    )
    # Default highlight lands on the first deterministic row, NOT a suggestion.
    assert dd.current() == "memory"
    # But the suggestions are reachable by navigating up.
    dd.move_up()
    assert dd.current() == "/parliament"
    dd.move_up()
    assert dd.current() == "/memory remember"
    # Clamped at the top (no wrap) since selection is required here.
    dd.move_up()
    assert dd.current() == "/memory remember"


def test_semantic_panel_opens_with_no_selection() -> None:
    dd = AutocompleteDropdown()
    dd.set_items(
        [
            DropdownRow("/memory forget", "drop a fact", ROW_SEMANTIC),
            DropdownRow("/memory remember", "store a fact", ROW_SEMANTIC),
        ],
        allow_no_selection=True,
    )
    # Nothing selected → Enter would submit the prose, not pick a row.
    assert dd.current() is None
    assert dd.current_row() is None
    # Down arms the first candidate.
    dd.move_down()
    assert dd.current() == "/memory forget"
    assert dd.current_row().kind == ROW_SEMANTIC
    # Up from the first row returns to "no selection".
    dd.move_up()
    assert dd.current() is None


def test_current_row_returns_kind() -> None:
    dd = AutocompleteDropdown()
    dd.set_items([DropdownRow("memory", "manage", "item")])
    row = dd.current_row()
    assert row is not None
    assert row.name == "memory"
    assert row.kind == "item"
