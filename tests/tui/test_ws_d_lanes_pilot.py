"""Layer 3 — full-app pilots for the WS-D AI lanes (issues 1 & 2).

Each spins a real Textual app, so kept to the integration truths the pure layers
can't prove:

1. ``/`` (low-commitment) shows the ☆ suggested lane above the deterministic
   commands, the lane is NEVER the default highlight, and selecting one only
   POPULATES the box (never fires).
2. The lane collapses on the first narrowing keystroke.
3. Forward ghost-text: ``/mem`` + Right-arrow accepts → ``/memory``.
4. Gait-read: a prose phrase opens the semantic panel with NO default selection
   (Enter submits the prose); Down+Enter POPULATES the box (never fires).
5. With both lanes off (the default), the dropdown is byte-identical — a prose
   phrase shows nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.commands.metadata import Arg, CommandMeta, SubCommand
from stackowl.commands.resolver import CommandCandidate
from stackowl.commands.sequence_store import SequenceSuggestion
from stackowl.events.bus import EventBus
from stackowl.tui.app import StackOwlApp
from stackowl.tui.widgets.autocomplete_dropdown import AutocompleteDropdown
from stackowl.tui.widgets.compose_helpers import CommandInfo
from stackowl.tui.widgets.submit_text_area import SubmitTextArea

pytestmark = pytest.mark.tui

_MEMORY_META = CommandMeta(
    grammar="verb",
    subcommands=(
        SubCommand("remember", "Store a fact", args=(Arg("text"),)),
        SubCommand("forget", "Drop a fact"),
    ),
)
_COMMAND_INFOS = [
    CommandInfo("memory", "Memory management", meta=_MEMORY_META),
    CommandInfo("parliament", "Run a parliament", meta=CommandMeta(grammar="leaf")),
]


class _FakeProvider:
    """Stand-in SequenceSuggestionProvider returning canned suggestions."""

    def __init__(self, suggestions: list[SequenceSuggestion]) -> None:
        self._suggestions = suggestions

    @property
    def owner_key(self) -> str:
        return "local"

    async def suggest(self, *, limit: int = 3) -> list[SequenceSuggestion]:
        return self._suggestions[:limit]


class _FakeResolver:
    """Stand-in CommandResolver returning canned candidates for any query."""

    def __init__(self, candidates: list[CommandCandidate]) -> None:
        self._candidates = candidates

    def index(self, commands: Any) -> None:  # noqa: ANN401 - test stub
        pass

    async def resolve(self, query: str, *, limit: int = 5) -> list[CommandCandidate]:
        return self._candidates[:limit]


def _app(**kwargs: Any) -> StackOwlApp:
    bus = kwargs.pop("event_bus", None) or EventBus()
    return StackOwlApp(bus, **kwargs)


def _dropdown_names(app: StackOwlApp) -> list[str]:
    dd = app.query_one("#autocomplete_dropdown", AutocompleteDropdown)
    return [dd._items[i][0] for i in range(dd.count)]


async def _type(pilot: Any, text: str) -> None:
    for ch in text:
        key = {"/": "slash", " ": "space"}.get(ch, ch)
        await pilot.press(key)


# --- Issue 1: ☆ suggested lane --------------------------------------------


@pytest.mark.asyncio
async def test_pilot_suggested_lane_appears_and_is_not_default() -> None:
    provider = _FakeProvider([SequenceSuggestion("/parliament", 3)])
    app = _app(command_infos=_COMMAND_INFOS, sequence_provider=provider)
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one("#compose_input", SubmitTextArea).focus()
        await pilot.pause()
        await _type(pilot, "/")
        await pilot.pause()
        await pilot.pause()

        dd = app.query_one("#autocomplete_dropdown", AutocompleteDropdown)
        assert dd.display is True
        names = _dropdown_names(app)
        # The suggestion is present, fenced above the deterministic commands.
        assert "/parliament" in names
        assert "memory" in names
        # ...but it is NEVER the default highlight — a real command is.
        assert dd.current() == "memory"


@pytest.mark.asyncio
async def test_pilot_suggested_lane_select_populates_never_fires() -> None:
    provider = _FakeProvider([SequenceSuggestion("/parliament", 3)])
    app = _app(command_infos=_COMMAND_INFOS, sequence_provider=provider)
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()
        await _type(pilot, "/")
        await pilot.pause()
        await pilot.pause()

        await pilot.press("up")  # climb into the suggested lane
        await pilot.pause()
        dd = app.query_one("#autocomplete_dropdown", AutocompleteDropdown)
        assert dd.current() == "/parliament"
        await pilot.press("enter")
        await pilot.pause()
        # Populated as editable text — NOT executed (no submit/clear).
        assert editor.text == "/parliament "


@pytest.mark.asyncio
async def test_pilot_suggested_lane_collapses_on_keystroke() -> None:
    provider = _FakeProvider([SequenceSuggestion("/parliament", 3)])
    app = _app(command_infos=_COMMAND_INFOS, sequence_provider=provider)
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one("#compose_input", SubmitTextArea).focus()
        await pilot.pause()
        await _type(pilot, "/m")
        await pilot.pause()
        await pilot.pause()
        names = _dropdown_names(app)
        # Narrowed to commands; the suggested lane is gone.
        assert "/parliament" not in names
        assert "memory" in names


# --- Issue 2: forward ghost-text ------------------------------------------


@pytest.mark.asyncio
async def test_pilot_ghost_text_accept_on_right() -> None:
    app = _app(command_infos=_COMMAND_INFOS)
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()
        await _type(pilot, "/mem")
        await pilot.pause()
        # Ghost predicts "ory"; Right-arrow accepts it.
        await pilot.press("right")
        await pilot.pause()
        assert editor.text == "/memory"


# --- Issue 2: gait-read semantic panel ------------------------------------


@pytest.mark.asyncio
async def test_pilot_prose_opens_semantic_panel_no_default() -> None:
    resolver = _FakeResolver(
        [CommandCandidate("/memory forget", "Drop a fact", 0.9, "verb")]
    )
    app = _app(command_infos=_COMMAND_INFOS, semantic_resolver=resolver)
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()
        await _type(pilot, "forget")
        await pilot.pause()
        await pilot.pause()

        dd = app.query_one("#autocomplete_dropdown", AutocompleteDropdown)
        assert dd.display is True
        assert "/memory forget" in _dropdown_names(app)
        # No default selection → Enter would submit the prose, not pick a row.
        assert dd.current() is None


@pytest.mark.asyncio
async def test_pilot_prose_enter_submits_does_not_select() -> None:
    resolver = _FakeResolver(
        [CommandCandidate("/memory forget", "Drop a fact", 0.9, "verb")]
    )
    app = _app(command_infos=_COMMAND_INFOS, semantic_resolver=resolver)
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()
        await _type(pilot, "forget")
        await pilot.pause()
        await pilot.pause()
        await pilot.press("enter")  # nothing selected → submit prose
        await pilot.pause()
        # Submitting clears the editor; the box was NOT replaced by a command.
        assert editor.text == ""


@pytest.mark.asyncio
async def test_pilot_prose_down_enter_populates_never_fires() -> None:
    resolver = _FakeResolver(
        [CommandCandidate("/memory forget", "Drop a fact", 0.9, "verb")]
    )
    app = _app(command_infos=_COMMAND_INFOS, semantic_resolver=resolver)
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()
        await _type(pilot, "forget")
        await pilot.pause()
        await pilot.pause()
        await pilot.press("down")  # arm the first candidate
        await pilot.pause()
        await pilot.press("enter")  # select → populate
        await pilot.pause()
        assert editor.text == "/memory forget "


# --- Kill-switch: byte-identical baseline when off ------------------------


@pytest.mark.asyncio
async def test_pilot_no_resolver_means_prose_shows_nothing() -> None:
    # No semantic_resolver wired (the default) → prose shows no dropdown.
    app = _app(command_infos=_COMMAND_INFOS)
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one("#compose_input", SubmitTextArea).focus()
        await pilot.pause()
        await _type(pilot, "forget")
        await pilot.pause()
        dd = app.query_one("#autocomplete_dropdown", AutocompleteDropdown)
        assert dd.display is False
