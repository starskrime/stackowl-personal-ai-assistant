"""Story 4 — mounted chat bubbles replace the single RichLog transcript."""

from __future__ import annotations

import asyncio

import pytest
from rich.text import Text

from stackowl.events.bus import EventBus
from stackowl.tui.app import StackOwlApp
from stackowl.tui.i18n import localize
from stackowl.tui.messages import ResponseChunkMessage, UserTurnMessage
from stackowl.tui.widgets.conversation_view import ConversationView
from stackowl.tui.widgets.message_bubble import MessageBubble, MessageRow

pytestmark = pytest.mark.tui


async def _pump(pilot: object) -> None:
    await pilot.pause()  # type: ignore[attr-defined]
    await asyncio.sleep(0.05)
    await pilot.pause()  # type: ignore[attr-defined]


def _body_text(bubble: MessageBubble) -> Text:
    """Extract the body Text from a bubble's rendered Group(label, body).

    The body is a plain ``rich.text.Text`` (never markup-parsed), so its
    ``.plain`` equals the raw buffer verbatim — proving no markup injection.
    """
    group = bubble.render()
    body = list(group.renderables)[1]  # type: ignore[attr-defined]
    assert isinstance(body, Text)
    return body


# ---------------------------------------------------------------------------
# A. MessageBubble unit behaviour
# ---------------------------------------------------------------------------


def test_append_accumulates_into_buffer() -> None:
    bubble = MessageBubble(role="agent", owl_name="secretary")
    bubble.append("hello ")
    bubble.append("world")
    assert bubble._buffer == "hello world"


@pytest.mark.asyncio
async def test_append_updates_body_after_mount() -> None:
    """After mount, multiple appends accumulate into the single bubble body."""
    bus = EventBus()
    app = StackOwlApp(event_bus=bus)
    async with app.run_test(size=(100, 40)) as pilot:
        view = app.query_one(ConversationView)
        container = view.query_one(f"#{'transcript'}")
        bubble = MessageBubble(role="agent", owl_name="secretary")
        row = MessageRow(bubble, role="agent")
        container.mount(row)
        await _pump(pilot)

        bubble.append("foo ")
        bubble.append("bar")
        await _pump(pilot)

        assert bubble._buffer == "foo bar"
        assert _body_text(bubble).plain == "foo bar"


def test_user_role_class_and_plain_body() -> None:
    bubble = MessageBubble(role="user", text="x")
    assert bubble.has_class("-user")
    # Body is plain Text (no markup parsing) — buffer verbatim.
    assert _body_text(bubble).plain == "x"


def test_agent_role_class() -> None:
    bubble = MessageBubble(role="agent", owl_name="secretary")
    assert bubble.has_class("-agent")


def test_user_label_is_localized() -> None:
    bubble = MessageBubble(role="user", text="hi")
    assert bubble._label == localize("transcript.role.you")


def test_agent_label_is_owl_name_not_localized() -> None:
    bubble = MessageBubble(role="agent", owl_name="parliament")
    assert bubble._label == "parliament"


def test_message_row_carries_role_class() -> None:
    user_row = MessageRow(MessageBubble(role="user", text="x"), role="user")
    agent_row = MessageRow(MessageBubble(role="agent", owl_name="o"), role="agent")
    assert user_row.has_class("-user")
    assert agent_row.has_class("-agent")


def test_row_alignment_css_present() -> None:
    css = MessageRow.DEFAULT_CSS
    assert "align-horizontal: right" in css  # user → right
    assert "align-horizontal: left" in css  # agent → left


# ---------------------------------------------------------------------------
# B. Pilot-driven layout + streaming integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_right_agent_left_alignment() -> None:
    """A user turn and an agent chunk mount as right- / left-aligned rows."""
    bus = EventBus()
    app = StackOwlApp(event_bus=bus)
    async with app.run_test(size=(100, 40)) as pilot:
        view = app.query_one(ConversationView)
        app.deliver(UserTurnMessage(text="hello there"))
        app.deliver(
            ResponseChunkMessage(text="hi back", owl_name="secretary", trace_id="t1")
        )
        await _pump(pilot)

        rows = list(view.query(MessageRow))
        user_rows = [r for r in rows if r.has_class("-user")]
        agent_rows = [r for r in rows if r.has_class("-agent")]
        assert len(user_rows) == 1
        assert len(agent_rows) == 1

        bubbles = list(view.query(MessageBubble))
        user_bubble = next(b for b in bubbles if b.has_class("-user"))
        agent_bubble = next(b for b in bubbles if b.has_class("-agent"))
        container = view.query_one(f"#{'transcript'}")

        # Geometry: the user bubble hugs the right edge, the agent the left.
        c_right = container.region.right
        c_left = container.region.x
        assert user_bubble.region.right >= agent_bubble.region.right
        assert agent_bubble.region.x <= user_bubble.region.x
        # Robust fallback alignment proof via CSS classes.
        assert user_bubble.region.right <= c_right
        assert agent_bubble.region.x >= c_left


@pytest.mark.asyncio
async def test_user_text_with_bracket_renders_verbatim() -> None:
    bus = EventBus()
    app = StackOwlApp(event_bus=bus)
    async with app.run_test(size=(100, 40)) as pilot:
        view = app.query_one(ConversationView)
        app.deliver(UserTurnMessage(text="hi [bot] there"))
        await _pump(pilot)

        bubble = next(b for b in view.query(MessageBubble) if b.has_class("-user"))
        assert "hi [bot] there" in bubble._buffer
        # Body is plain Text → the '[' survives verbatim, no markup parsing.
        assert _body_text(bubble).plain == "hi [bot] there"


@pytest.mark.asyncio
async def test_same_trace_streams_into_one_bubble_new_trace_opens_another() -> None:
    """Same trace_id → one bubble accumulating; new trace_id → a second bubble."""
    bus = EventBus()
    app = StackOwlApp(event_bus=bus)
    async with app.run_test(size=(100, 40)) as pilot:
        view = app.query_one(ConversationView)
        app.deliver(
            ResponseChunkMessage(text="alpha ", owl_name="secretary", trace_id="A")
        )
        app.deliver(
            ResponseChunkMessage(text="beta", owl_name="secretary", trace_id="A")
        )
        await _pump(pilot)

        agent_bubbles = [b for b in view.query(MessageBubble) if b.has_class("-agent")]
        assert len(agent_bubbles) == 1
        assert agent_bubbles[0]._buffer == "alpha beta"

        app.deliver(
            ResponseChunkMessage(text="gamma", owl_name="secretary", trace_id="B")
        )
        await _pump(pilot)

        agent_bubbles = [b for b in view.query(MessageBubble) if b.has_class("-agent")]
        assert len(agent_bubbles) == 2
        assert agent_bubbles[1]._buffer == "gamma"
