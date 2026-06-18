"""Tests for TuiAssembly + StackOwlApp wiring (Commit D)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from stackowl.events.bus import EventBus
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.tui.app import StackOwlApp
from stackowl.tui.assembly import TuiAssembly, TuiComponents
from stackowl.tui.widgets.compose_helpers import CommandInfo

pytestmark = pytest.mark.tui


def test_build_returns_frozen_components() -> None:
    bus = EventBus()
    components = TuiAssembly.build(event_bus=bus)
    assert isinstance(components, TuiComponents)
    with pytest.raises(Exception):
        components.app = None  # type: ignore[misc]


def test_build_constructs_app_with_passed_autocomplete_names() -> None:
    bus = EventBus()
    components = TuiAssembly.build(
        event_bus=bus,
        command_names=["help", "memory", "tier"],
        owl_names=["secretary", "scout"],
    )
    assert isinstance(components.app, StackOwlApp)
    assert components.app._command_names == ["help", "memory", "tier"]
    assert components.app._owl_names == ["secretary", "scout"]


def test_build_threads_command_infos() -> None:
    bus = EventBus()
    components = TuiAssembly.build(
        event_bus=bus,
        command_infos=[CommandInfo("help", "List commands")],
    )
    assert components.app._command_infos == [CommandInfo("help", "List commands")]


def test_build_attaches_coordinator_to_app() -> None:
    bus = EventBus()
    components = TuiAssembly.build(event_bus=bus)
    assert components.coordinator._app is components.app
    assert components.coordinator._event_bus is bus


def test_app_compose_yields_five_widgets() -> None:
    """Banner + ParliamentPanel + ConversationView + PipelineStrip + ComposeArea."""
    from stackowl.tui.widgets.banner import Banner
    from stackowl.tui.widgets.compose_area import ComposeArea
    from stackowl.tui.widgets.conversation_view import ConversationView
    from stackowl.tui.widgets.parliament_panel import ParliamentPanel
    from stackowl.tui.widgets.pipeline_strip import PipelineStrip

    bus = EventBus()
    app = StackOwlApp(event_bus=bus)
    widgets = list(app.compose())
    types = {type(w) for w in widgets}
    assert Banner in types
    assert ComposeArea in types
    assert ConversationView in types
    assert ParliamentPanel in types
    assert PipelineStrip in types
    assert len(widgets) == 5
    # Banner is the pinned top zone — must be yielded first.
    assert isinstance(widgets[0], Banner)


def test_compose_submitted_message_republishes_on_event_bus() -> None:
    """When ComposeArea bubbles ComposeSubmittedMessage, app republishes via EventBus."""
    from stackowl.tui.messages import ComposeSubmittedMessage

    bus = EventBus()
    received: list[dict[str, object]] = []
    bus.subscribe("compose_submitted", lambda payload: received.append(payload))

    app = StackOwlApp(event_bus=bus)
    app.on_compose_submitted_message(ComposeSubmittedMessage(text="hello world"))

    assert received == [{"text": "hello world"}]


@pytest.mark.asyncio
async def test_cli_adapter_receive_picks_up_compose_event() -> None:
    """CLIAdapter subscribed to compose_submitted; receive() awaits the queue."""
    from stackowl.channels.cli_adapter import CLIAdapter

    bus = EventBus()
    components = TuiAssembly.build(event_bus=bus, command_names=[], owl_names=[])
    adapter = CLIAdapter(
        session_id="test-cli", tui_components=components, event_bus=bus,
    )

    # Simulate ComposeArea submitting via the app's republish path.
    bus.emit("compose_submitted", {"text": "from compose area"})

    msg = await adapter.receive()
    assert msg.text == "from compose area"
    assert msg.channel == "cli"
    assert msg.session_id == "test-cli"
    assert msg.trace_id.startswith("cli-")


@pytest.mark.asyncio
async def test_cli_adapter_send_publishes_response_chunk_events() -> None:
    """send() iterates chunks and publishes each as a response_chunk event."""
    from stackowl.channels.cli_adapter import CLIAdapter

    bus = EventBus()
    components = TuiAssembly.build(event_bus=bus, command_names=[], owl_names=[])
    adapter = CLIAdapter(
        session_id="test-send", tui_components=components, event_bus=bus,
    )

    received: list[dict[str, object]] = []
    bus.subscribe("response_chunk", lambda payload: received.append(payload))

    async def _chunks() -> AsyncIterator[ResponseChunk]:
        yield ResponseChunk(
            content="hello ", is_final=False, chunk_index=0,
            trace_id="t1", owl_name="secretary",
        )
        yield ResponseChunk(
            content="world", is_final=True, chunk_index=1,
            trace_id="t1", owl_name="secretary",
        )

    await adapter.send(_chunks())

    assert len(received) == 2
    assert received[0]["text"] == "hello "
    assert received[1]["text"] == "world"
    assert all(r["owl_name"] == "secretary" for r in received)
    assert received[0]["trace_id"] == "t1"


@pytest.mark.asyncio
async def test_cli_adapter_legacy_mode_when_no_tui_components() -> None:
    """Without tui_components, CLIAdapter falls back to legacy RichLog mode."""
    from stackowl.channels.cli_adapter import CLIAdapter, _LegacyStackOwlApp

    adapter = CLIAdapter(session_id="test-legacy")
    assert adapter._mode == "raw"
    assert isinstance(adapter._app, _LegacyStackOwlApp)


def test_cli_adapter_full_mode_when_tui_components_given() -> None:
    """With tui_components + event_bus, CLIAdapter uses the 4-zone app."""
    from stackowl.channels.cli_adapter import CLIAdapter

    bus = EventBus()
    components = TuiAssembly.build(event_bus=bus)
    adapter = CLIAdapter(
        session_id="test-full", tui_components=components, event_bus=bus,
    )
    assert adapter._mode == "fullzone"
    assert adapter._app is components.app


@pytest.mark.asyncio
async def test_cli_adapter_compose_event_text_drives_queue() -> None:
    """The internal _input_queue gets populated by the compose_submitted callback."""
    from stackowl.channels.cli_adapter import CLIAdapter

    bus = EventBus()
    components = TuiAssembly.build(event_bus=bus)
    adapter = CLIAdapter(
        session_id="test-q", tui_components=components, event_bus=bus,
    )

    # Two messages — must be received in order.
    bus.emit("compose_submitted", {"text": "first"})
    bus.emit("compose_submitted", {"text": "second"})

    first = await asyncio.wait_for(adapter.receive(), timeout=1.0)
    second = await asyncio.wait_for(adapter.receive(), timeout=1.0)
    assert first.text == "first"
    assert second.text == "second"
