"""Compose redesign — palette is actually visible above the input; minimal layout."""

from __future__ import annotations

import asyncio

import pytest
from textual.containers import Vertical
from textual.widgets import Static

from stackowl.events.bus import EventBus
from stackowl.tui.app import StackOwlApp
from stackowl.tui.glyphs import GLYPH_PROMPT
from stackowl.tui.i18n import localize
from stackowl.tui.widgets.autocomplete_dropdown import AutocompleteDropdown
from stackowl.tui.widgets.compose_area import ComposeArea
from stackowl.tui.widgets.submit_text_area import SubmitTextArea

pytestmark = pytest.mark.tui

_CMD_INFOS = [
    ("memory", "Memory: stats, search, forget"),
    ("parliament", "Start a multi-owl debate"),
    ("tools", "List registered tools"),
]


def _app() -> StackOwlApp:
    from stackowl.tui.widgets.compose_helpers import CommandInfo

    return StackOwlApp(
        EventBus(),
        command_infos=[CommandInfo(n, d) for n, d in _CMD_INFOS],
        owl_names=["athena"],
    )


async def _pump(pilot: object) -> None:
    await pilot.pause()  # type: ignore[attr-defined]
    await asyncio.sleep(0.05)
    await pilot.pause()  # type: ignore[attr-defined]


def test_compose_area_is_vertical_container() -> None:
    # Stacked children only size correctly inside a container (bare Widget → 0).
    assert issubclass(ComposeArea, Vertical)


def test_dropdown_css_has_no_clipping_overlay_pattern() -> None:
    # Guard against the regression: the broken version used layer/dock/offset
    # which placed the palette outside its parent and got it clipped.
    css = AutocompleteDropdown.DEFAULT_CSS
    assert "offset-y" not in css
    assert "dock:" not in css
    assert "layer:" not in css


@pytest.mark.asyncio
async def test_dropdown_is_visible_above_editor_not_clipped() -> None:
    """Pressing '/' shows the palette ON SCREEN, above the input — not clipped.

    This is the assertion the original implementation lacked: it only checked the
    dropdown's logical coordinates, not that it was within the screen and unclipped.
    """
    app = _app()
    async with app.run_test(size=(96, 30)) as pilot:
        editor = app.query_one(SubmitTextArea)
        editor.focus()
        await pilot.press("/")
        await _pump(pilot)

        dropdown = app.query_one(AutocompleteDropdown)
        screen = app.screen.region
        assert dropdown.display is True
        assert dropdown.region.height > 0
        assert dropdown.region.width > 0
        # Fully within the screen (not clipped off an edge)...
        assert dropdown.region.y >= screen.y
        assert dropdown.region.bottom <= screen.bottom
        # ...and rendered ABOVE the editor (a floating palette).
        assert dropdown.region.y < editor.region.y
        # Shows the commands with descriptions.
        assert dropdown.count == len(_CMD_INFOS)


@pytest.mark.asyncio
async def test_prompt_and_hint_render() -> None:
    app = _app()
    async with app.run_test(size=(96, 30)) as pilot:
        await _pump(pilot)
        area = app.query_one(ComposeArea)
        prompt = area.query_one("#compose_prompt", Static)
        hint = area.query_one("#compose_hint", Static)
        assert str(GLYPH_PROMPT) in str(prompt.render())
        assert localize("compose.hints") in str(hint.render())


@pytest.mark.asyncio
async def test_hint_doubles_as_state_indicator() -> None:
    app = _app()
    async with app.run_test(size=(96, 30)) as pilot:
        await _pump(pilot)
        area = app.query_one(ComposeArea)
        hint = area.query_one("#compose_hint", Static)

        area.set_mcp_disabled(True)
        assert localize("compose.mcp_disabled") in str(hint.render())
        area.set_mcp_disabled(False)
        assert localize("compose.hints") in str(hint.render())

        area.set_parliament_active(True)
        assert localize("compose.parliament_active") in str(hint.render())
        area.set_parliament_active(False)
        assert localize("compose.hints") in str(hint.render())


def test_screen_overflow_hidden_so_borders_are_flush() -> None:
    # A reserved screen scrollbar column insets every right border by one;
    # the fully-docked layout never scrolls, so the Screen overflow is hidden.
    css = StackOwlApp.CSS
    assert "Screen" in css
    assert "overflow: hidden" in css


@pytest.mark.asyncio
async def test_hiding_palette_clears_items_and_hides() -> None:
    """Closing the palette drops its rows (no ghost text) and hides it."""
    app = _app()
    async with app.run_test(size=(96, 30)) as pilot:
        editor = app.query_one(SubmitTextArea)
        editor.focus()
        await pilot.press("/")
        await _pump(pilot)
        dropdown = app.query_one(AutocompleteDropdown)
        assert dropdown.display is True
        assert dropdown.count > 0

        # Delete the '/', which dismisses the palette.
        await pilot.press("backspace")
        await _pump(pilot)
        assert dropdown.display is False
        assert dropdown.count == 0  # items cleared → nothing left to ghost


@pytest.mark.asyncio
async def test_editor_autogrows_with_content_clamped() -> None:
    app = _app()
    async with app.run_test(size=(96, 30)) as pilot:
        await _pump(pilot)
        area = app.query_one(ComposeArea)
        editor = app.query_one(SubmitTextArea)

        editor.text = "one"
        area._autogrow(editor)
        await _pump(pilot)
        assert editor.region.height == 1

        editor.text = "a\nb\nc\nd"
        area._autogrow(editor)
        await _pump(pilot)
        assert editor.region.height == 4

        editor.text = "\n".join(str(i) for i in range(40))
        area._autogrow(editor)
        await _pump(pilot)
        assert editor.region.height == 8  # clamped to _MAX_INPUT_ROWS
