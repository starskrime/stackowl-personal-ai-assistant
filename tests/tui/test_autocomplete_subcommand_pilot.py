"""Layer 3 — full-app pilots for context-aware sub-command autocomplete.

Slow (each spins a real Textual app), so kept to the three integration truths
the pure/state layers can't prove:

1. Typing ``/memory`` + space descends into memory's sub-commands.
2. ``down`` + ``enter`` on a sub-command composes ``/memory <sub> ``.
3. A flag-grammar command (``/quiet ``) shows NO selectable sub rows (honesty).
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.commands.metadata import Arg, CommandMeta, SubCommand
from stackowl.events.bus import EventBus
from stackowl.tui.app import StackOwlApp
from stackowl.tui.widgets.autocomplete_dropdown import AutocompleteDropdown
from stackowl.tui.widgets.compose_area import ComposeArea
from stackowl.tui.widgets.compose_helpers import CommandInfo
from stackowl.tui.widgets.submit_text_area import SubmitTextArea

pytestmark = pytest.mark.tui

_MEMORY_META = CommandMeta(
    grammar="verb",
    subcommands=(
        SubCommand("stats", "Show memory stats"),
        SubCommand("search", "Search facts", args=(Arg("query"),)),
        SubCommand("export", "Export memory"),
    ),
)
_QUIET_META = CommandMeta(grammar="flag", args=(Arg("minutes", required=False),))

_COMMAND_INFOS = [
    CommandInfo("memory", "Memory management", meta=_MEMORY_META),
    CommandInfo("quiet", "Mute notifications", meta=_QUIET_META),
]


def _app(**kwargs: Any) -> StackOwlApp:
    bus = kwargs.pop("event_bus", None) or EventBus()
    return StackOwlApp(bus, **kwargs)


def _dropdown_names(app: StackOwlApp) -> list[str]:
    dd = app.query_one("#autocomplete_dropdown", AutocompleteDropdown)
    return [dd._items[i][0] for i in range(dd.count)]


@pytest.mark.asyncio
async def test_pilot_space_after_command_shows_subcommands() -> None:
    app = _app(command_infos=_COMMAND_INFOS)
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one("#compose_input", SubmitTextArea).focus()
        await pilot.pause()
        for ch in "/memory":
            await pilot.press(ch if ch != "/" else "slash")
        await pilot.press("space")
        await pilot.pause()

        dd = app.query_one("#autocomplete_dropdown", AutocompleteDropdown)
        assert dd.display is True
        names = _dropdown_names(app)
        assert "stats" in names
        assert "search" in names
        assert "export" in names


@pytest.mark.asyncio
async def test_pilot_down_enter_composes_subcommand() -> None:
    app = _app(command_infos=_COMMAND_INFOS)
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()
        for ch in "/memory":
            await pilot.press(ch if ch != "/" else "slash")
        await pilot.press("space")
        await pilot.pause()

        await pilot.press("down")  # highlight "search"
        await pilot.pause()
        await pilot.press("enter")  # complete the sub-command
        await pilot.pause()

        # "search" takes a <query> arg → trailing space, command prefix kept.
        assert editor.text == "/memory search "


@pytest.mark.asyncio
async def test_pilot_flag_command_shows_no_subcommands() -> None:
    app = _app(command_infos=_COMMAND_INFOS)
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one("#compose_input", SubmitTextArea).focus()
        await pilot.pause()
        for ch in "/quiet":
            await pilot.press(ch if ch != "/" else "slash")
        await pilot.press("space")
        await pilot.pause()

        dd = app.query_one("#autocomplete_dropdown", AutocompleteDropdown)
        # flag grammar → no selectable sub rows; dropdown hidden.
        assert dd.display is False
        assert app.query_one(ComposeArea)._completion_level.value == "none"
