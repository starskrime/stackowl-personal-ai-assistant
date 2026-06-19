"""Dispatch test — /agent is wired through CommandRegistry."""
from __future__ import annotations

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401

_USAGE_MARKER = "/agent create"
_NO_PENDING = "No pending agent proposal"


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


async def test_agent_no_subcommand_returns_usage() -> None:
    """Empty args returns the usage block (no crash)."""
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("agent", "", make_state())
    assert _USAGE_MARKER in result


async def test_agent_cancel_no_pending() -> None:
    """/agent cancel with nothing pending returns the no-pending message."""
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("agent", "cancel", make_state())
    assert _NO_PENDING in result


async def test_agent_confirm_no_scheduler() -> None:
    """/agent confirm without scheduler returns the not-configured message (no crash)."""
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("agent", "confirm", make_state())
    # Scheduler is checked first; without one we get "Scheduler not configured"
    assert "not configured" in result or _NO_PENDING in result


async def test_agent_not_found_when_not_registered() -> None:
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("agent", "", make_state())
