"""/find — natural-language command discovery, driven through the real registry."""

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
        owl_name="system",
        pipeline_step="receive",
    )


async def _dispatch(args: str) -> str:
    return await CommandRegistry.instance().dispatch("find", args, _state())


@pytest.mark.asyncio
async def test_find_is_registered_and_flag_grammar() -> None:
    cmd = CommandRegistry.instance().get("find")
    assert cmd is not None
    assert cmd.meta.grammar == "flag"
    assert cmd.meta.subcommands == ()  # honesty: no fake sub-commands


@pytest.mark.asyncio
async def test_find_suggests_a_structured_command() -> None:
    out = await _dispatch("forget what I told you")
    assert "/memory forget" in out
    # honest: it offers, it does not run
    assert "press enter to run it" in out.lower()


@pytest.mark.asyncio
async def test_find_empty_shows_usage() -> None:
    out = await _dispatch("")
    assert "Usage: /find" in out


@pytest.mark.asyncio
async def test_find_no_match_points_to_help() -> None:
    out = await _dispatch("zzzqqq nonsense xyzzy")
    assert "/help" in out
