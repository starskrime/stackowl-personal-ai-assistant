"""Dispatch tests — /urgent broadcasts to real channel roster.

The original code defaulted channels=["cli"] and assembly.py passed no
channel list → /urgent only ever reached CLI despite its "all channels"
description.  The fix resolves the roster from the live ChannelRegistry at
dispatch time, falling back to ["cli"] only when the registry is empty.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.channels.registry import ChannelRegistry
from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_router(deliver_result: object = None) -> MagicMock:
    router = MagicMock()
    router.deliver = AsyncMock(return_value=deliver_result)
    return router


def _make_channel_adapter(name: str) -> MagicMock:
    adapter = MagicMock()
    adapter.channel_name = name
    return adapter


@pytest.fixture(autouse=True)
def _reset_registries() -> None:
    CommandRegistry.reset()
    ChannelRegistry.instance().reset()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_urgent_targets_live_registry_channels() -> None:
    """When ChannelRegistry has telegram + cli adapters, /urgent reaches both."""
    ChannelRegistry.instance().register(_make_channel_adapter("cli"))
    ChannelRegistry.instance().register(_make_channel_adapter("telegram"))

    router = _make_router()
    deps = CommandDeps(router=router)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "urgent", "system alert", make_state()
    )

    # Delivered to both channels — deliver called twice
    assert router.deliver.call_count == 2
    called_channels = {
        call.args[0].channel_name for call in router.deliver.call_args_list
    }
    assert called_channels == {"cli", "telegram"}
    assert "broadcast to 2" in result


async def test_urgent_fallback_to_cli_when_registry_empty() -> None:
    """When ChannelRegistry is empty, /urgent falls back to ['cli']."""
    # Registry is empty (reset by autouse fixture)
    router = _make_router()
    deps = CommandDeps(router=router)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "urgent", "fallback alert", make_state()
    )

    assert router.deliver.call_count == 1
    called_channels = {
        call.args[0].channel_name for call in router.deliver.call_args_list
    }
    assert called_channels == {"cli"}
    assert "broadcast to 1" in result


async def test_urgent_not_configured_when_router_none() -> None:
    """When no router is provided, /urgent returns honest not-configured."""
    deps = CommandDeps(router=None)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "urgent", "hello", make_state()
    )

    assert "not configured" in result


async def test_urgent_requires_message() -> None:
    """Empty message returns usage hint."""
    router = _make_router()
    deps = CommandDeps(router=router)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "urgent", "   ", make_state()
    )

    assert "message required" in result
    assert router.deliver.call_count == 0


async def test_urgent_description_says_all_registered_channels() -> None:
    """The description must not claim 'all channels' while only targeting cli."""
    from stackowl.commands.urgent_command import UrgentCommand

    cmd = UrgentCommand()
    # Lock the honest qualifier: the old false description said "all channels"
    # but only targeted cli. "registered" must be present so a regression to the
    # bare overclaim is caught.
    assert "registered" in cmd.description
    assert "cli" not in cmd.description, (
        "Description must not hard-code 'cli' — that was the overclaim"
    )
