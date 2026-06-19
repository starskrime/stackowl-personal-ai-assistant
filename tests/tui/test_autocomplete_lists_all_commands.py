"""Regression: the slash autocomplete must surface ALL registered commands.

Bug: the TUI "/" dropdown showed only the first 8 of 29 registered commands —
``filter_candidates`` / ``filter_command_infos`` defaulted ``limit=8`` and the
``AutocompleteDropdown`` (a ``Static``) clipped at ``max-height`` without
scrolling the highlight into view. Both caps are exercised below against a
29-command surface (the real shipped count).
"""

from __future__ import annotations

import pytest

from stackowl.events.bus import EventBus
from stackowl.tui.app import StackOwlApp
from stackowl.tui.widgets.autocomplete_dropdown import AutocompleteDropdown
from stackowl.tui.widgets.compose_area import ComposeArea
from stackowl.tui.widgets.compose_helpers import (
    CommandInfo,
    build_state,
    filter_candidates,
    filter_command_infos,
)
from stackowl.tui.widgets.submit_text_area import SubmitTextArea

pytestmark = pytest.mark.tui

_N = 29


def _infos() -> list[CommandInfo]:
    return [CommandInfo(f"cmd{i:02d}", f"desc {i}") for i in range(_N)]


def _names() -> list[str]:
    return [ci.name for ci in _infos()]


# ---------------------------------------------------------------------------
# A. Pure filters no longer truncate the whole surface on empty prefix
# ---------------------------------------------------------------------------


def test_filter_command_infos_empty_prefix_returns_all_commands() -> None:
    out = filter_command_infos("", _infos())
    assert len(out) == _N


def test_filter_candidates_empty_prefix_returns_all_commands() -> None:
    out = filter_candidates("", _names())
    assert len(out) == _N


def test_build_state_command_empty_prefix_returns_all_commands() -> None:
    state = build_state("/", command_names=_names(), owl_names=[])
    assert len(state.candidates) == _N


# ---------------------------------------------------------------------------
# B. Dropdown keeps the highlight inside the visible window (scrolls)
# ---------------------------------------------------------------------------


def test_dropdown_visible_range_follows_highlight_down() -> None:
    dd = AutocompleteDropdown()
    dd.set_items([(f"cmd{i:02d}", None) for i in range(_N)])
    for _ in range(_N - 1):
        dd.move_down()
    assert dd.highlight == _N - 1
    start, end = dd.visible_range()
    assert start <= dd.highlight < end  # highlight stays visible


def test_dropdown_visible_range_follows_highlight_back_up() -> None:
    dd = AutocompleteDropdown()
    dd.set_items([(f"cmd{i:02d}", None) for i in range(_N)])
    for _ in range(_N - 1):
        dd.move_down()
    for _ in range(_N - 1):
        dd.move_up()
    assert dd.highlight == 0
    start, end = dd.visible_range()
    assert start <= dd.highlight < end
    assert start == 0


# ---------------------------------------------------------------------------
# C. End-to-end pilot — typing "/" surfaces every command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pilot_slash_surfaces_all_29_commands() -> None:
    app = StackOwlApp(EventBus(), command_infos=_infos())
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one("#compose_input", SubmitTextArea).focus()
        await pilot.pause()
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.text = "/"
        area = app.query_one(ComposeArea)
        fake = type("E", (), {"text_area": type("TA", (), {"text": "/"})()})()
        area.on_text_area_changed(fake)  # type: ignore[arg-type]
        await pilot.pause()

        dropdown = app.query_one("#autocomplete_dropdown", AutocompleteDropdown)
        assert dropdown.display is True
        assert dropdown.count == _N
