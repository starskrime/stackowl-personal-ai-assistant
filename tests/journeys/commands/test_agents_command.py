"""Dispatch test — /agents is wired through CommandRegistry."""
from __future__ import annotations

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401

_NO_SCHEDULER = "(scheduler not wired — cannot manage agents)"


class _FakeScheduler:
    """Minimal fake scheduler that returns an empty job list."""

    async def list_jobs(self) -> list:
        return []


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


async def test_agents_list_no_scheduler() -> None:
    """Without a scheduler, /agents list returns the not-configured message."""
    deps = CommandDeps(scheduler=None)
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("agents", "list", make_state())
    assert _NO_SCHEDULER in result


async def test_agents_list_empty_scheduler() -> None:
    """With a scheduler that has no jobs, /agents list returns a non-error string."""
    deps = CommandDeps(scheduler=_FakeScheduler())
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("agents", "list", make_state())
    # format_jobs_table with empty list — no crash, some content
    assert _NO_SCHEDULER not in result


async def test_agents_no_subcommand_returns_usage() -> None:
    """Empty args returns the usage block."""
    deps = CommandDeps(scheduler=None)
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("agents", "", make_state())
    assert "Usage:" in result


async def test_agents_not_found_when_not_registered() -> None:
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("agents", "list", make_state())
