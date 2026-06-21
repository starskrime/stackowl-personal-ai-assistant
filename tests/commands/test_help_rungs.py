"""Three-rung /help — index, command page, sub-command page.

Drives the real registry via register_all_commands so /help reflects exactly
what ships.
"""

from __future__ import annotations

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry


@pytest.fixture(autouse=True)
def _isolate_registry():  # type: ignore[no-untyped-def]
    snapshot = list(CommandRegistry.instance().list())
    CommandRegistry.reset()
    register_all_commands(CommandDeps())
    yield
    CommandRegistry.reset()
    for cmd in snapshot:
        CommandRegistry.instance().register(cmd)


def _state():  # type: ignore[no-untyped-def]
    from stackowl.pipeline.state import PipelineState

    return PipelineState(
        trace_id="t",
        session_id="s",
        input_text="",
        channel="cli",
        owl_name="Daria",
        pipeline_step="receive",
    )


def _help():  # type: ignore[no-untyped-def]
    from stackowl.commands.help_command import HelpCommand

    return HelpCommand()


@pytest.mark.asyncio
async def test_rung1_index_groups_and_marks_subcommands() -> None:
    out = await _help().handle("", _state())
    assert "Available commands:" in out
    # /memory has sub-commands → marked with ▸
    assert "/memory ▸" in out
    # the navigation bridge to rung 2
    assert "/help <command>" in out


@pytest.mark.asyncio
async def test_rung2_command_page_lists_subcommands_with_summaries() -> None:
    out = await _help().handle("memory", _state())
    assert out.startswith("/memory —")
    assert "SUBCOMMANDS" in out
    assert "search" in out and "forget" in out
    # bridge to rung 3
    assert "/help memory <subcommand>" in out


@pytest.mark.asyncio
async def test_rung2_accepts_leading_slash() -> None:
    out = await _help().handle("/memory", _state())
    assert out.startswith("/memory —")


@pytest.mark.asyncio
async def test_rung3_subcommand_page_shows_usage_and_args() -> None:
    out = await _help().handle("memory search", _state())
    assert out.startswith("/memory search —")
    assert "USAGE" in out
    assert "<query>" in out  # the declared arg signature


@pytest.mark.asyncio
async def test_rung3_two_level_browser_branch() -> None:
    """/help browser profile renders the branch + its children (list, delete)."""
    out = await _help().handle("browser profile", _state())
    assert out.startswith("/browser profile —")
    assert "list" in out and "delete" in out


@pytest.mark.asyncio
async def test_unknown_command_points_to_help() -> None:
    out = await _help().handle("nope", _state())
    assert "Unknown command" in out and "/help" in out


@pytest.mark.asyncio
async def test_unknown_subcommand_falls_back_to_command_page() -> None:
    out = await _help().handle("memory bogus", _state())
    assert "Unknown sub-command" in out
    assert "SUBCOMMANDS" in out  # still shows the command page so they can recover
