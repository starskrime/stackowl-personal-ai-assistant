"""Dispatch test — /agent manage half (list/...) is wired through CommandRegistry.

The former /agents command was merged into /agent; these cover the manage subcommands.
"""
from __future__ import annotations

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401

_NO_SCHEDULER = "✗ scheduler not wired — cannot manage agents."


class _FakeScheduler:
    """Minimal fake scheduler that returns an empty job list."""

    async def list_jobs(self) -> list:
        return []


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


async def test_agent_list_no_scheduler() -> None:
    """Without a scheduler, /agent list returns the not-configured message."""
    deps = CommandDeps(scheduler=None)
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("agent", "list", make_state())
    assert _NO_SCHEDULER in result


async def test_agent_list_empty_scheduler() -> None:
    """With a scheduler that has no jobs, /agent list returns a non-error string."""
    deps = CommandDeps(scheduler=_FakeScheduler())
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("agent", "list", make_state())
    assert _NO_SCHEDULER not in result


async def test_agent_no_subcommand_returns_usage() -> None:
    """Empty args returns the usage block listing BOTH halves."""
    deps = CommandDeps(scheduler=None)
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("agent", "", make_state())
    assert "Usage:" in result
    # unified surface advertises both create and manage subcommands
    assert "create" in result
    assert "list" in result


async def test_agents_command_is_gone() -> None:
    """The old /agents surface must no longer exist — it was merged into /agent."""
    register_all_commands(CommandDeps(scheduler=_FakeScheduler()), registry=CommandRegistry.instance())
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("agents", "list", make_state())
