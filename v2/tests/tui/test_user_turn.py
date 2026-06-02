"""Story 3 — the user's own submitted turn is echoed into the transcript."""

from __future__ import annotations

import dataclasses

import pytest
from rich.text import Text

from stackowl.events.bus import EventBus
from stackowl.tui.app import StackOwlApp
from stackowl.tui.messages import ComposeSubmittedMessage, UserTurnMessage
from stackowl.tui.widgets.conversation_view import ConversationView
from stackowl.tui.widgets.message_bubble import MessageBubble

pytestmark = pytest.mark.tui


def test_user_turn_message_is_frozen_dataclass() -> None:
    msg = UserTurnMessage(text="hello")
    assert dataclasses.is_dataclass(msg)
    assert msg.text == "hello"
    with pytest.raises(dataclasses.FrozenInstanceError):
        msg.text = "other"  # type: ignore[misc]


def test_submit_still_emits_event_when_view_unmounted() -> None:
    """Self-healing: with no mounted transcript, the EventBus emit still fires."""
    bus = EventBus()
    received: list[dict[str, object]] = []
    bus.subscribe("compose_submitted", lambda payload: received.append(payload))

    app = StackOwlApp(event_bus=bus)
    # App not mounted → query_one(ConversationView) raises, is caught.
    app.on_compose_submitted_message(ComposeSubmittedMessage(text="hi"))

    assert received == [{"text": "hi"}]


@pytest.mark.asyncio
async def test_user_turn_rendered_verbatim_in_transcript() -> None:
    """Submitting mounts the user's turn as a bubble, markup NOT parsed."""
    bus = EventBus()
    received: list[dict[str, object]] = []
    bus.subscribe("compose_submitted", lambda payload: received.append(payload))

    app = StackOwlApp(event_bus=bus)
    async with app.run_test(size=(100, 40)) as pilot:
        # Includes a '[' which would corrupt rendering if treated as markup.
        app.on_compose_submitted_message(ComposeSubmittedMessage(text="hi [bot] there"))
        await pilot.pause()

        view = app.query_one(ConversationView)
        bubbles = list(view.query(MessageBubble))
        user_bubbles = [b for b in bubbles if b.has_class("-user")]
        assert len(user_bubbles) == 1
        bubble = user_bubbles[0]
        # Verbatim buffer — the '[' survives, markup left intact.
        assert "hi [bot] there" in bubble._buffer
        # The body is rendered as plain rich Text (no markup parsing), so the
        # '[' cannot inject markup — the rendered body equals the raw text.
        body = list(bubble.render().renderables)[1]  # type: ignore[attr-defined]
        assert isinstance(body, Text)
        assert body.plain == "hi [bot] there"

    # The CLIAdapter path is independent and must still receive the turn.
    assert received == [{"text": "hi [bot] there"}]
