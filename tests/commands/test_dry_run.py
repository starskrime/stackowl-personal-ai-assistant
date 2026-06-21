"""Dry-run preview — `??` previews a command without running its handler."""

from __future__ import annotations

import pytest

from stackowl.commands.base import SlashCommand
from stackowl.commands.dry_run import build_preview, strip_sigil
from stackowl.commands.metadata import CommandMeta, SubCommand
from stackowl.commands.registry import CommandRegistry


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


def test_strip_sigil_only_matches_trailing_token() -> None:
    assert strip_sigil("forget abc??") == (True, "forget abc")
    assert strip_sigil("forget abc ??") == (True, "forget abc")
    assert strip_sigil("memory??") == (True, "memory")
    assert strip_sigil("??") == (True, "")
    assert strip_sigil("forget abc") == (False, "forget abc")
    # A '?' that isn't the dry-run sigil is left alone.
    assert strip_sigil("search what?") == (False, "search what?")


def test_build_preview_resolves_subcommand() -> None:
    class _Cmd(SlashCommand):
        @property
        def command(self) -> str:
            return "memory"

        @property
        def description(self) -> str:
            return "Memory commands"

        @property
        def meta(self) -> CommandMeta:
            return CommandMeta(
                subcommands=(SubCommand("forget", "Delete a fact by id", description="Removes it."),)
            )

        async def handle(self, args, state):  # type: ignore[no-untyped-def]
            raise AssertionError("handle must not run during preview")

    out = build_preview("memory", _Cmd(), "forget abc")
    assert "/memory forget" in out
    assert "Delete a fact by id" in out
    assert "nothing has run" in out
    assert "Arguments: abc" in out


class _SpyCommand(SlashCommand):
    """Records whether its handler was invoked."""

    def __init__(self) -> None:
        self.ran = False

    @property
    def command(self) -> str:
        return "spy"

    @property
    def description(self) -> str:
        return "Spy command"

    @property
    def meta(self) -> CommandMeta:
        return CommandMeta(subcommands=(SubCommand("go", "Do the thing"),))

    async def handle(self, args, state):  # type: ignore[no-untyped-def]
        self.ran = True
        return "ran"


@pytest.fixture(autouse=True)
def _isolate_registry():  # type: ignore[no-untyped-def]
    snapshot = list(CommandRegistry.instance().list())
    CommandRegistry.reset()
    yield
    CommandRegistry.reset()
    for cmd in snapshot:
        CommandRegistry.instance().register(cmd)


@pytest.mark.asyncio
async def test_dry_run_does_not_call_handler() -> None:
    spy = _SpyCommand()
    CommandRegistry.instance().register(spy)
    out = await CommandRegistry.instance().dispatch("spy", "go??", _state())
    assert spy.ran is False  # the whole point: no side effects
    assert "Preview" in out and "/spy go" in out


@pytest.mark.asyncio
async def test_without_sigil_handler_runs_normally() -> None:
    spy = _SpyCommand()
    CommandRegistry.instance().register(spy)
    out = await CommandRegistry.instance().dispatch("spy", "go", _state())
    assert spy.ran is True
    assert out == "ran"
