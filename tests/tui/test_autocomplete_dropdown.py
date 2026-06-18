"""Story 7 — real, visible, navigable autocomplete dropdown.

Mixes pure-unit coverage of :class:`AutocompleteDropdown` (set_items / move /
current / clamp) with full-app pilots that prove the overlay is actually visible,
keyboard-navigable, completes the editor text, posts the (now non-orphan)
:class:`AutocompleteSelectedMessage`, and — critically — that Enter completes when
the dropdown is OPEN but still submits when it is CLOSED.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.events.bus import EventBus
from stackowl.tui.app import StackOwlApp
from stackowl.tui.messages import AutocompleteSelectedMessage
from stackowl.tui.widgets.autocomplete_dropdown import AutocompleteDropdown
from stackowl.tui.widgets.compose_area import ComposeArea
from stackowl.tui.widgets.compose_helpers import CommandInfo
from stackowl.tui.widgets.submit_text_area import SubmitTextArea

pytestmark = pytest.mark.tui

_COMMAND_INFOS = [
    CommandInfo("help", "List commands"),
    CommandInfo("history", "Show history"),
    CommandInfo("memory", "Memory mgmt"),
]


def _app(**kwargs: Any) -> StackOwlApp:
    """Build a StackOwlApp with a fresh EventBus (the required first arg)."""
    bus = kwargs.pop("event_bus", None) or EventBus()
    return StackOwlApp(bus, **kwargs)


# ---------------------------------------------------------------------------
# A. Pure unit — AutocompleteDropdown data model
# ---------------------------------------------------------------------------


def test_dropdown_set_items_resets_highlight_and_count() -> None:
    dd = AutocompleteDropdown()
    dd.set_items([("a", "alpha"), ("b", None)])
    assert dd.count == 2
    assert not dd.is_empty
    assert dd.highlight == 0
    assert dd.current() == "a"


def test_dropdown_move_down_then_current() -> None:
    dd = AutocompleteDropdown()
    dd.set_items([("a", None), ("b", None), ("c", None)])
    dd.move_down()
    assert dd.current() == "b"
    dd.move_down()
    assert dd.current() == "c"


def test_dropdown_move_down_clamps_at_bottom() -> None:
    dd = AutocompleteDropdown()
    dd.set_items([("a", None), ("b", None)])
    dd.move_down()
    dd.move_down()
    dd.move_down()  # already at bottom — no wrap
    assert dd.current() == "b"


def test_dropdown_move_up_clamps_at_top() -> None:
    dd = AutocompleteDropdown()
    dd.set_items([("a", None), ("b", None)])
    dd.move_down()
    dd.move_up()
    dd.move_up()  # already at top — no wrap
    assert dd.current() == "a"


def test_dropdown_empty_current_is_none() -> None:
    dd = AutocompleteDropdown()
    assert dd.is_empty
    assert dd.count == 0
    assert dd.current() is None
    # Moving on an empty list must not raise.
    dd.move_down()
    dd.move_up()
    assert dd.current() is None


# ---------------------------------------------------------------------------
# B. Pilots — visible overlay + command completion
# ---------------------------------------------------------------------------


def _set_editor_text(app: StackOwlApp, text: str) -> SubmitTextArea:
    """Set editor text and fire the change handler so the dropdown updates."""
    editor = app.query_one("#compose_input", SubmitTextArea)
    editor.text = text
    area = app.query_one(ComposeArea)
    fake = type(
        "E", (), {"text_area": type("TA", (), {"text": text})()}
    )()
    area.on_text_area_changed(fake)  # type: ignore[arg-type]
    return editor


@pytest.mark.asyncio
async def test_pilot_command_dropdown_visible_with_description() -> None:
    app = _app(command_infos=_COMMAND_INFOS)
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one("#compose_input", SubmitTextArea).focus()
        await pilot.pause()
        _set_editor_text(app, "/h")
        await pilot.pause()

        dropdown = app.query_one("#autocomplete_dropdown", AutocompleteDropdown)
        assert dropdown.display is True
        # "help" + "history" match "/h"; "memory" does not.
        names = [dropdown._items[i][0] for i in range(dropdown.count)]
        assert "help" in names
        assert "history" in names
        assert "memory" not in names
        # Description is carried through for the command rows.
        descs = {n: d for n, d in dropdown._items}
        assert descs["help"] == "List commands"
        # Overlay region is actually painted (non-empty, on-screen).
        assert dropdown.region.width > 0
        assert dropdown.region.height > 0


@pytest.mark.asyncio
async def test_pilot_down_then_enter_completes_second_command() -> None:
    app = _app(command_infos=_COMMAND_INFOS)
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()
        _set_editor_text(app, "/h")
        await pilot.pause()

        await pilot.press("down")  # highlight "history"
        await pilot.pause()
        await pilot.press("enter")  # complete (NOT submit)
        await pilot.pause()

        assert editor.text == "/history "
        dropdown = app.query_one("#autocomplete_dropdown", AutocompleteDropdown)
        assert dropdown.display is False


@pytest.mark.asyncio
async def test_pilot_tab_completes_first_command() -> None:
    app = _app(command_infos=_COMMAND_INFOS)
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()
        _set_editor_text(app, "/h")
        await pilot.pause()

        await pilot.press("tab")
        await pilot.pause()

        assert editor.text == "/help "


@pytest.mark.asyncio
async def test_pilot_escape_hides_dropdown() -> None:
    app = _app(command_infos=_COMMAND_INFOS)
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()
        _set_editor_text(app, "/h")
        await pilot.pause()
        dropdown = app.query_one("#autocomplete_dropdown", AutocompleteDropdown)
        assert dropdown.display is True

        await pilot.press("escape")
        await pilot.pause()

        assert dropdown.display is False


@pytest.mark.asyncio
async def test_pilot_non_matching_prefix_hides_dropdown() -> None:
    app = _app(command_infos=_COMMAND_INFOS)
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one("#compose_input", SubmitTextArea).focus()
        await pilot.pause()
        _set_editor_text(app, "/zzz")  # nothing starts with "zzz"
        await pilot.pause()

        dropdown = app.query_one("#autocomplete_dropdown", AutocompleteDropdown)
        assert dropdown.display is False


# ---------------------------------------------------------------------------
# C. AutocompleteSelectedMessage is now actually posted (orphan closed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pilot_completion_posts_autocomplete_selected_message() -> None:
    captured: list[AutocompleteSelectedMessage] = []

    class _CaptureApp(StackOwlApp):
        def on_autocomplete_selected_message(
            self, message: AutocompleteSelectedMessage
        ) -> None:
            captured.append(message)

    app = _CaptureApp(EventBus(), command_infos=_COMMAND_INFOS)
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one("#compose_input", SubmitTextArea).focus()
        await pilot.pause()
        _set_editor_text(app, "/me")  # matches "memory"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

    assert len(captured) == 1
    assert captured[0].selected == "memory"
    assert captured[0].completion_type == "command"


def test_autocomplete_selected_message_is_not_frozen() -> None:
    # A frozen message crashes Textual's pump during bubbling — assert mutability.
    msg = AutocompleteSelectedMessage(selected="x", completion_type="owl")
    msg._stop_propagation = True  # would raise on a frozen dataclass
    assert msg._stop_propagation is True


# ---------------------------------------------------------------------------
# D. Enter completes-when-open vs submits-when-closed (regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pilot_enter_submits_when_dropdown_closed() -> None:
    bus = EventBus()
    submitted: list[str] = []
    bus.subscribe("compose_submitted", lambda p: submitted.append(p["text"]))

    app = _app(event_bus=bus, command_infos=_COMMAND_INFOS)
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()
        # Plain text, no trigger → no dropdown.
        await pilot.press("h", "i")
        await pilot.pause()
        dropdown = app.query_one("#autocomplete_dropdown", AutocompleteDropdown)
        assert dropdown.display is False

        await pilot.press("enter")
        await pilot.pause()

        assert submitted == ["hi"]
        assert editor.text == ""


@pytest.mark.asyncio
async def test_pilot_enter_completes_not_submits_when_dropdown_open() -> None:
    bus = EventBus()
    submitted: list[str] = []
    bus.subscribe("compose_submitted", lambda p: submitted.append(p["text"]))

    app = _app(event_bus=bus, command_infos=_COMMAND_INFOS)
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()
        _set_editor_text(app, "/h")
        await pilot.pause()

        await pilot.press("enter")  # dropdown open → completes, no submit
        await pilot.pause()

        assert submitted == []  # nothing submitted
        assert editor.text == "/help "


# ---------------------------------------------------------------------------
# E. Owl (@-mention) dropdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pilot_owl_dropdown_appears_and_completes() -> None:
    app = _app(owl_names=["athena", "atlas"])
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()
        _set_editor_text(app, "@at")
        await pilot.pause()

        dropdown = app.query_one("#autocomplete_dropdown", AutocompleteDropdown)
        assert dropdown.display is True
        names = [dropdown._items[i][0] for i in range(dropdown.count)]
        assert "athena" in names
        assert "atlas" in names
        # Owl rows carry no description.
        assert all(d is None for _, d in dropdown._items)

        await pilot.press("enter")
        await pilot.pause()
        assert editor.text == "@athena "


@pytest.mark.asyncio
async def test_pilot_owl_completion_preserves_preceding_text() -> None:
    app = _app(owl_names=["athena"])
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()
        _set_editor_text(app, "hello @at")
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()
        assert editor.text == "hello @athena "
