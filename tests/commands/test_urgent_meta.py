"""Metadata contract for /urgent — flag grammar, no fake subcommands."""

from __future__ import annotations

import pytest

from stackowl.commands.metadata import render_usage
from stackowl.commands.urgent_command import UrgentCommand
from stackowl.pipeline.state import PipelineState


def _meta():  # type: ignore[no-untyped-def]
    return UrgentCommand(router=None).meta


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
    assert [a.name for a in _meta().args] == ["message"]


def test_group() -> None:
    assert _meta().group == "Notifications"


@pytest.mark.asyncio
async def test_empty_message_shows_usage() -> None:
    cmd = UrgentCommand(router=object(), channels=["cli"])  # type: ignore[arg-type]
    result = await cmd.handle("   ", _state())
    assert "message required" in result
    assert render_usage("urgent", cmd.meta) in result
    assert "<message>" in result
