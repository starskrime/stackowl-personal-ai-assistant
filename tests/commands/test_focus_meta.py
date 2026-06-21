"""Metadata contract for /focus — flag grammar, no fake subcommands."""

from __future__ import annotations

import pytest

from stackowl.commands.focus_command import FocusCommand
from stackowl.commands.metadata import render_usage
from stackowl.pipeline.state import PipelineState


class _StubRouter:
    def __init__(self) -> None:
        self.mode: str | None = None

    def set_focus_mode(self, mode: str) -> None:
        self.mode = mode


class _StubBus:
    def emit(self, event: str, payload: dict[str, object]) -> None:  # noqa: D401
        pass


def _meta():  # type: ignore[no-untyped-def]
    return FocusCommand().meta


def _state() -> PipelineState:
    return PipelineState(
        trace_id="t",
        session_id="s",
        input_text="",
        channel="cli",
        owl_name="Daria",
        pipeline_step="receive",
    )


def test_grammar_is_flag() -> None:
    assert _meta().grammar == "flag"


def test_declares_no_fake_subcommands() -> None:
    assert _meta().subcommands == ()


def test_args_declared() -> None:
    args = _meta().args
    assert [a.name for a in args] == ["mode"]
    assert args[0].choices == ("soft", "hard", "off")


def test_group() -> None:
    assert _meta().group == "Focus & Availability"


@pytest.mark.asyncio
async def test_empty_args_defaults_to_soft() -> None:
    router = _StubRouter()
    cmd = FocusCommand(router=router, event_bus=_StubBus())  # type: ignore[arg-type]
    result = await cmd.handle("", _state())
    assert result == "focus_mode:soft"
    assert router.mode == "soft"


@pytest.mark.asyncio
async def test_unrecognized_mode_returns_usage() -> None:
    cmd = FocusCommand(router=_StubRouter(), event_bus=_StubBus())  # type: ignore[arg-type]
    result = await cmd.handle("bananas", _state())
    assert result == render_usage("focus", cmd.meta)
    assert "soft|hard|off" in result
