"""Plan C Task 4 — CommandResponse actions render as TUI Button widgets.

Mirrors ``tests/tui/test_is_final.py``'s shape: message-model unit tests,
``build_message`` unit tests, a ``CLIAdapter.send`` forwarding test, then
Pilot-driven integration tests through the real ``StackOwlApp`` +
``UIStateCoordinator`` + ``EventBus``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button

from stackowl.commands.response import CANCEL_SENTINEL, Action
from stackowl.events.bus import EventBus
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.tui.app import StackOwlApp
from stackowl.tui.coordinator import UIStateCoordinator
from stackowl.tui.coordinator_messages import build_message
from stackowl.tui.messages import ResponseChunkMessage
from stackowl.tui.messages.compose import ComposeSubmittedMessage
from stackowl.tui.widgets.conversation_view import ConversationView
from stackowl.tui.widgets.message_bubble import ActionButtonRow, MessageBubble, _ActionButton

pytestmark = pytest.mark.tui


async def _pump(pilot: object) -> None:
    await pilot.pause()  # type: ignore[attr-defined]
    await asyncio.sleep(0.05)
    await pilot.pause()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# A. Message-model + coordinator wiring
# ---------------------------------------------------------------------------


def test_response_chunk_message_actions_default_empty() -> None:
    assert ResponseChunkMessage(text="x", owl_name="o").actions == ()


def test_build_message_propagates_actions() -> None:
    action = Action(label="Go", command="/help")
    msg = build_message(
        "response_chunk",
        {"text": "pick one", "owl_name": "o", "actions": (action,)},
    )
    assert isinstance(msg, ResponseChunkMessage)
    assert msg.actions == (action,)


def test_build_message_actions_default_empty() -> None:
    msg = build_message("response_chunk", {"text": "x", "owl_name": "o"})
    assert isinstance(msg, ResponseChunkMessage)
    assert msg.actions == ()


def _chunks(*specs: tuple[str, tuple[Action, ...]]) -> AsyncIterator[ResponseChunk]:
    async def _gen() -> AsyncIterator[ResponseChunk]:
        for i, (content, actions) in enumerate(specs):
            yield ResponseChunk(
                content=content, is_final=False, chunk_index=i,
                trace_id="t1", owl_name="system", actions=actions,
            )
    return _gen()


@pytest.mark.asyncio
async def test_send_forwards_actions() -> None:
    from stackowl.channels.cli_adapter import CLIAdapter
    from stackowl.tui.assembly import TuiAssembly

    action = Action(label="Go", command="/help")
    bus = EventBus()
    components = TuiAssembly.build(event_bus=bus, command_names=[], owl_names=[])
    adapter = CLIAdapter(session_id="t", tui_components=components, event_bus=bus)
    received: list[dict[str, object]] = []
    bus.subscribe("response_chunk", lambda p: received.append(p))

    await adapter.send(_chunks(("pick one", (action,))))

    assert received[0]["actions"] == (action,)


# ---------------------------------------------------------------------------
# B. ActionButtonRow unit behaviour (bare Textual harness)
# ---------------------------------------------------------------------------


class _Harness(App[None]):
    """Minimal app hosting one ActionButtonRow, capturing replayed commands."""

    def __init__(self, actions: tuple[Action, ...]) -> None:
        super().__init__()
        self._actions = actions
        self.posted: list[ComposeSubmittedMessage] = []

    def compose(self) -> ComposeResult:
        yield ActionButtonRow(self._actions)

    def on_compose_submitted_message(self, message: ComposeSubmittedMessage) -> None:
        self.posted.append(message)


@pytest.mark.asyncio
async def test_action_button_row_renders_one_button_per_action() -> None:
    action = Action(label="Go", command="/help")
    app = _Harness((action,))
    async with app.run_test():
        widget = app.query_one(ActionButtonRow)
        buttons = list(widget.query(Button))
        assert len(buttons) == 1
        assert buttons[0].label == "Go"


@pytest.mark.asyncio
async def test_press_non_destructive_button_posts_compose_submitted() -> None:
    action = Action(label="Go", command="/help")
    app = _Harness((action,))
    async with app.run_test() as pilot:
        button = app.query_one(_ActionButton)
        await pilot.click(button)
        await _pump(pilot)

        assert [m.text for m in app.posted] == ["/help"]


@pytest.mark.asyncio
async def test_destructive_press_shows_confirm_row() -> None:
    action = Action(label="Delete", command="/delete-all", destructive=True)
    app = _Harness((action,))
    async with app.run_test() as pilot:
        row = app.query_one(ActionButtonRow)
        first = app.query_one(_ActionButton)
        await pilot.click(first)
        await _pump(pilot)

        buttons = list(row.query(_ActionButton))
        assert len(buttons) == 2
        commands = {b.action_data.command for b in buttons}
        assert commands == {"/delete-all", CANCEL_SENTINEL}
        # Nothing dispatched yet — the first tap only opened the confirm.
        assert app.posted == []


@pytest.mark.asyncio
async def test_cancel_neutralizes_confirm_row_entirely() -> None:
    """Cancel must genuinely remove the Yes button, not just hide it.

    Same class of bug the Telegram review caught (a stale Yes button still
    tappable after Cancel) — here the button is unmounted from the DOM, so a
    real user click can no longer reach it, and the row's `_resolved` guard
    blocks a stale programmatic re-delivery too (see next test).
    """
    action = Action(label="Delete", command="/delete-all", destructive=True)
    app = _Harness((action,))
    async with app.run_test() as pilot:
        row = app.query_one(ActionButtonRow)
        first = app.query_one(_ActionButton)
        await pilot.click(first)
        await _pump(pilot)

        cancel = next(
            b for b in row.query(_ActionButton) if b.action_data.command == CANCEL_SENTINEL
        )
        await pilot.click(cancel)
        await _pump(pilot)

        assert list(row.query(_ActionButton)) == []
        assert row._resolved is True
        # Cancel never dispatches anything.
        assert app.posted == []


@pytest.mark.asyncio
async def test_second_press_after_resolve_is_noop() -> None:
    """A press that lands after the row already resolved must not re-fire —
    guards the race between a click and the remove/mount it triggers."""
    action = Action(label="Go", command="/help")
    app = _Harness((action,))
    async with app.run_test() as pilot:
        row = app.query_one(ActionButtonRow)
        button = app.query_one(_ActionButton)
        event = Button.Pressed(button)

        await row.on_button_pressed(event)
        await row.on_button_pressed(event)  # simulated stale re-delivery
        await _pump(pilot)

        assert [m.text for m in app.posted] == ["/help"]


# ---------------------------------------------------------------------------
# C. End-to-end: EventBus -> coordinator -> ConversationView -> tap -> replay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_command_reply_actions_render_as_buttons_in_transcript() -> None:
    action = Action(label="Go", command="/help")
    bus = EventBus()
    app = StackOwlApp(event_bus=bus)
    async with app.run_test(size=(100, 40)) as pilot:
        coord = UIStateCoordinator(app=app, event_bus=bus)
        await coord.start()
        try:
            bus.emit(
                "response_chunk",
                {"text": "pick one", "owl_name": "system", "actions": (action,)},
            )
            await _pump(pilot)

            view = app.query_one(ConversationView)
            buttons = list(view.query(_ActionButton))
            assert len(buttons) == 1
            assert buttons[0].label == "Go"
        finally:
            await coord.stop()


@pytest.mark.asyncio
async def test_tapping_replayed_button_echoes_command_into_transcript() -> None:
    """Pressing a rendered button replays through the SAME path a typed
    command uses: ComposeSubmittedMessage -> StackOwlApp -> echoed as a
    user bubble in the transcript (and republished on the EventBus)."""
    action = Action(label="Go", command="/help")
    bus = EventBus()
    app = StackOwlApp(event_bus=bus)
    compose_events: list[dict[str, object]] = []
    bus.subscribe("compose_submitted", lambda p: compose_events.append(p))
    async with app.run_test(size=(100, 40)) as pilot:
        coord = UIStateCoordinator(app=app, event_bus=bus)
        await coord.start()
        try:
            bus.emit(
                "response_chunk",
                {"text": "pick one", "owl_name": "system", "actions": (action,)},
            )
            await _pump(pilot)

            view = app.query_one(ConversationView)
            button = view.query_one(_ActionButton)
            await pilot.click(button)
            await _pump(pilot)

            user_bubbles = [b for b in view.query(MessageBubble) if b.has_class("-user")]
            assert len(user_bubbles) == 1
            assert user_bubbles[0]._buffer == "/help"
            assert compose_events == [{"text": "/help"}]
        finally:
            await coord.stop()
