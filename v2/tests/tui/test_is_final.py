"""Story 5 — is_final turn-end wiring (pipeline → coordinator → bubble close)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from stackowl.events.bus import EventBus
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.tui.app import StackOwlApp
from stackowl.tui.coordinator_messages import build_message
from stackowl.tui.messages import ResponseChunkMessage
from stackowl.tui.widgets.conversation_view import ConversationView
from stackowl.tui.widgets.message_bubble import MessageBubble

pytestmark = pytest.mark.tui


async def _pump(pilot: object) -> None:
    await pilot.pause()  # type: ignore[attr-defined]
    await asyncio.sleep(0.05)
    await pilot.pause()  # type: ignore[attr-defined]


def test_response_chunk_message_is_final_defaults_false() -> None:
    assert ResponseChunkMessage(text="x", owl_name="o").is_final is False
    assert ResponseChunkMessage(text="x", owl_name="o", is_final=True).is_final is True


def test_build_message_propagates_is_final() -> None:
    msg = build_message("response_chunk", {"text": "x", "owl_name": "o", "is_final": True})
    assert isinstance(msg, ResponseChunkMessage)
    assert msg.is_final is True


@pytest.mark.asyncio
async def test_is_final_closes_bubble_same_trace_opens_another() -> None:
    """A final chunk closes the turn — a later same-trace chunk opens a 2nd bubble."""
    app = StackOwlApp(event_bus=EventBus())
    async with app.run_test(size=(100, 40)) as pilot:
        view = app.query_one(ConversationView)
        app.deliver(
            ResponseChunkMessage(text="done.", owl_name="o", trace_id="A", is_final=True)
        )
        await _pump(pilot)
        app.deliver(ResponseChunkMessage(text="again", owl_name="o", trace_id="A"))
        await _pump(pilot)

        agent_bubbles = [b for b in view.query(MessageBubble) if b.has_class("-agent")]
        # Without is_final closing the turn, the repeated trace would reuse one
        # bubble; the close forces a second.
        assert len(agent_bubbles) == 2


@pytest.mark.asyncio
async def test_empty_terminal_marker_creates_no_bubble() -> None:
    """A standalone empty is_final marker (nothing open) must not spawn a bubble."""
    app = StackOwlApp(event_bus=EventBus())
    async with app.run_test(size=(100, 40)) as pilot:
        view = app.query_one(ConversationView)
        app.deliver(
            ResponseChunkMessage(text="", owl_name="o", trace_id="A", is_final=True)
        )
        await _pump(pilot)
        assert len([b for b in view.query(MessageBubble) if b.has_class("-agent")]) == 0


def _chunks(*specs: tuple[str, bool]) -> AsyncIterator[ResponseChunk]:
    async def _gen() -> AsyncIterator[ResponseChunk]:
        for i, (content, final) in enumerate(specs):
            yield ResponseChunk(
                content=content, is_final=final, chunk_index=i,
                trace_id="t1", owl_name="secretary",
            )
    return _gen()


@pytest.mark.asyncio
async def test_send_forwards_is_final_and_synthesizes_terminal_marker() -> None:
    from stackowl.channels.cli_adapter import CLIAdapter
    from stackowl.tui.assembly import TuiAssembly

    bus = EventBus()
    components = TuiAssembly.build(event_bus=bus, command_names=[], owl_names=[])
    adapter = CLIAdapter(session_id="t", tui_components=components, event_bus=bus)
    received: list[dict[str, object]] = []
    bus.subscribe("response_chunk", lambda p: received.append(p))

    # No chunk flagged final → a synthetic empty is_final marker is appended.
    await adapter.send(_chunks(("a", False), ("b", False)))
    assert [r["is_final"] for r in received] == [False, False, True]
    assert received[-1]["text"] == ""
    assert received[-1]["trace_id"] == "t1"


@pytest.mark.asyncio
async def test_send_no_synthetic_marker_when_last_chunk_final() -> None:
    from stackowl.channels.cli_adapter import CLIAdapter
    from stackowl.tui.assembly import TuiAssembly

    bus = EventBus()
    components = TuiAssembly.build(event_bus=bus, command_names=[], owl_names=[])
    adapter = CLIAdapter(session_id="t", tui_components=components, event_bus=bus)
    received: list[dict[str, object]] = []
    bus.subscribe("response_chunk", lambda p: received.append(p))

    await adapter.send(_chunks(("a", False), ("b", True)))
    # Last chunk already final → no extra marker emitted.
    assert [r["is_final"] for r in received] == [False, True]
