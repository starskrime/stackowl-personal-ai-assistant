"""Gateway-level delivery test — coordinator-built messages ACTUALLY render.

This is the integration test that was missing: it drives the REAL
:class:`UIStateCoordinator` + REAL :class:`StackOwlApp` (mounted via
``run_test``) + REAL :class:`EventBus`, then asserts the target widget actually
rendered/updated. The pre-existing bug (messages delivered via ``post_message``
never reach child-widget handlers, and FrozenMessage crashes the pump) is
caught here: test #1 fails unless delivery goes through ``StackOwlApp.deliver()``.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.events.bus import EventBus
from stackowl.tui.app import StackOwlApp
from stackowl.tui.coordinator import UIStateCoordinator
from stackowl.tui.messages import (
    BudgetAlertMessage,
    ResponseChunkMessage,
)
from stackowl.tui.widgets.compose_area import ComposeArea
from stackowl.tui.widgets.conversation_view import ConversationView
from stackowl.tui.widgets.message_bubble import MessageBubble
from stackowl.tui.widgets.pipeline_strip import PipelineStrip

pytestmark = pytest.mark.tui


async def _pump(pilot: object) -> None:
    """Advance the UI loop enough for debounced flushes to land."""
    await pilot.pause()  # type: ignore[attr-defined]
    await asyncio.sleep(0.05)
    await pilot.pause()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_response_chunk_renders_in_conversation_view() -> None:
    """THE proof the bug is fixed: an emitted response_chunk reaches a bubble."""
    bus = EventBus()
    app = StackOwlApp(event_bus=bus)
    async with app.run_test(size=(100, 40)) as pilot:
        coord = UIStateCoordinator(app=app, event_bus=bus)
        await coord.start()
        try:
            bus.emit("response_chunk", {"text": "RENDER_ME", "owl_name": "secretary"})
            await _pump(pilot)

            view = app.query_one(ConversationView)
            agent_bubbles = [
                b for b in view.query(MessageBubble) if b.has_class("-agent")
            ]
            assert len(agent_bubbles) == 1
            assert "RENDER_ME" in agent_bubbles[0]._buffer
        finally:
            await coord.stop()


@pytest.mark.asyncio
async def test_pipeline_step_updates_strip() -> None:
    """An emitted pipeline_step_changed updates the PipelineStrip reactive."""
    bus = EventBus()
    app = StackOwlApp(event_bus=bus)
    async with app.run_test(size=(100, 40)) as pilot:
        coord = UIStateCoordinator(app=app, event_bus=bus)
        await coord.start()
        try:
            bus.emit(
                "pipeline_step_changed",
                {"step_name": "memory", "step_index": 4, "total_steps": 8},
            )
            await _pump(pilot)

            assert app.query_one(PipelineStrip).step_name == "memory"
        finally:
            await coord.stop()


@pytest.mark.asyncio
async def test_deliver_orphan_logs_warning_does_not_crash() -> None:
    """An orphaned message type (no UI sink) is safe — deliver() never raises."""
    bus = EventBus()
    app = StackOwlApp(event_bus=bus)
    async with app.run_test(size=(100, 40)):
        # No exception expected — orphan path logs a warning and returns.
        app.deliver(BudgetAlertMessage(pct=0.8, cost_today=1.0))


@pytest.mark.asyncio
async def test_compose_area_state_message_disables_input() -> None:
    """An emitted mcp_spectator_active locks the compose area via deliver()."""
    bus = EventBus()
    app = StackOwlApp(event_bus=bus)
    async with app.run_test(size=(100, 40)) as pilot:
        coord = UIStateCoordinator(app=app, event_bus=bus)
        await coord.start()
        try:
            bus.emit("mcp_spectator_active", {})
            await _pump(pilot)

            assert app.query_one(ComposeArea).state == "mcp-disabled"
        finally:
            await coord.stop()


def test_deliver_self_heals_when_widget_absent() -> None:
    """deliver() on an UNMOUNTED app self-heals — query_one raises → caught."""
    bus = EventBus()
    app = StackOwlApp(event_bus=bus)
    # Not mounted → query_one(ConversationView) raises; deliver() must not.
    app.deliver(ResponseChunkMessage(text="x", owl_name="y"))
