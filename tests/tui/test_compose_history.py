"""Shell-style Up/Down history over this session's submitted messages.

Before this fix there was no history at all — Up/Down always fell straight
through to TextArea's own cursor movement (see ComposeArea._push_history /
_handle_history_key in compose_area.py).
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.events.bus import EventBus
from stackowl.tui.app import StackOwlApp
from stackowl.tui.widgets.compose_area import ComposeArea
from stackowl.tui.widgets.submit_text_area import SubmitTextArea

pytestmark = pytest.mark.tui


def _app(**kwargs: Any) -> StackOwlApp:
    bus = kwargs.pop("event_bus", None) or EventBus()
    return StackOwlApp(bus, **kwargs)


async def _type_and_submit(pilot: Any, text: str) -> None:
    for ch in text:
        await pilot.press(ch if ch != "/" else "slash")
    await pilot.press("enter")
    await pilot.pause()


@pytest.mark.asyncio
async def test_up_recalls_last_submitted_message() -> None:
    app = _app()
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()
        await _type_and_submit(pilot, "first message")
        await _type_and_submit(pilot, "second message")

        assert editor.text == ""
        await pilot.press("up")
        await pilot.pause()
        assert editor.text == "second message"


@pytest.mark.asyncio
async def test_up_up_down_walks_history_both_ways() -> None:
    app = _app()
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()
        await _type_and_submit(pilot, "first")
        await _type_and_submit(pilot, "second")

        await pilot.press("up")
        await pilot.pause()
        assert editor.text == "second"
        await pilot.press("up")
        await pilot.pause()
        assert editor.text == "first"
        await pilot.press("down")
        await pilot.pause()
        assert editor.text == "second"


@pytest.mark.asyncio
async def test_down_past_newest_restores_in_progress_draft() -> None:
    app = _app()
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()
        await _type_and_submit(pilot, "sent earlier")

        for ch in "draft text":
            await pilot.press(ch)
        await pilot.pause()
        assert editor.text == "draft text"

        await pilot.press("up")
        await pilot.pause()
        assert editor.text == "sent earlier"

        await pilot.press("down")
        await pilot.pause()
        assert editor.text == "draft text"


@pytest.mark.asyncio
async def test_up_with_no_history_falls_through_harmlessly() -> None:
    app = _app()
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        assert editor.text == ""


@pytest.mark.asyncio
async def test_repeated_identical_submit_does_not_duplicate_in_history() -> None:
    app = _app()
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()
        await _type_and_submit(pilot, "same thing")
        await _type_and_submit(pilot, "same thing")

        assert app.query_one(ComposeArea)._history == ["same thing"]
