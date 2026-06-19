"""Dispatch test — /bye trips the shutdown event through CommandRegistry."""
from __future__ import annotations

import asyncio

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


async def test_bye_sets_shutdown_event() -> None:
    """/bye must set the wired shutdown_event (the orchestrator's stop_event) and
    reply with a goodbye — the REAL side effect that triggers graceful teardown."""
    stop_event = asyncio.Event()
    register_all_commands(CommandDeps(shutdown_event=stop_event), registry=CommandRegistry.instance())

    assert not stop_event.is_set()
    result = await CommandRegistry.instance().dispatch("bye", "", make_state())

    assert stop_event.is_set(), "/bye must trip the shutdown event"
    assert "Goodbye" in result or "shutting down" in result.lower()


async def test_bye_honest_when_no_shutdown_wire() -> None:
    """With no shutdown_event wired, /bye degrades honestly (no crash, no false claim)."""
    register_all_commands(CommandDeps(shutdown_event=None), registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("bye", "", make_state())
    assert "not available" in result.lower() or "✗" in result
