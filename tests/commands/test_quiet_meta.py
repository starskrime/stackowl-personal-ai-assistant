"""Metadata contract for /quiet — flag grammar, no fake subcommands."""

from __future__ import annotations

import pytest

from stackowl.commands.metadata import render_usage
from stackowl.commands.quiet_command import QuietHoursCommand
from stackowl.pipeline.state import PipelineState


def _meta():  # type: ignore[no-untyped-def]
    return QuietHoursCommand(db=None).meta


def test_grammar_is_flag() -> None:
    assert _meta().grammar == "flag"


def test_declares_no_fake_subcommands() -> None:
    assert _meta().subcommands == ()


def test_args_declared() -> None:
    names = [a.name for a in _meta().args]
    assert names == ["start", "end", "--category"]


def test_group() -> None:
    assert _meta().group == "Notifications"


@pytest.mark.asyncio
async def test_empty_args_returns_rendered_usage() -> None:
    cmd = QuietHoursCommand(db=object())  # type: ignore[arg-type]
    state = PipelineState(
        trace_id="t",
        session_id="s",
        input_text="",
        channel="cli",
        owl_name="Daria",
        pipeline_step="receive",
    )
    result = await cmd.handle("", state)
    assert result == render_usage("quiet", cmd.meta)
    assert "<start>" in result
