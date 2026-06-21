"""Metadata contract for /connect and /disconnect — flag grammar, the service
is an OPERAND, never a sub-command. No fake subcommands."""

from __future__ import annotations

import pytest

from stackowl.commands.connect_command import ConnectCommand, DisconnectCommand
from stackowl.commands.metadata import render_usage
from stackowl.pipeline.state import PipelineState


def _state() -> PipelineState:
    return PipelineState(
        trace_id="t",
        session_id="s",
        input_text="",
        channel="cli",
        owl_name="Daria",
        pipeline_step="receive",
    )


# --- /connect -------------------------------------------------------------

def test_connect_grammar_is_flag() -> None:
    assert ConnectCommand(integration_registry=None).meta.grammar == "flag"


def test_connect_no_fake_subcommands() -> None:
    assert ConnectCommand(integration_registry=None).meta.subcommands == ()


def test_connect_args_declared() -> None:
    args = ConnectCommand(integration_registry=None).meta.args
    assert [a.name for a in args] == ["service"]
    assert args[0].required is False


def test_connect_group() -> None:
    assert ConnectCommand(integration_registry=None).meta.group == "Integrations"


# --- /disconnect ----------------------------------------------------------

def test_disconnect_grammar_is_flag() -> None:
    assert DisconnectCommand(integration_registry=None).meta.grammar == "flag"


def test_disconnect_no_fake_subcommands() -> None:
    assert DisconnectCommand(integration_registry=None).meta.subcommands == ()


def test_disconnect_args_declared() -> None:
    args = DisconnectCommand(integration_registry=None).meta.args
    assert [a.name for a in args] == ["service"]
    assert args[0].required is False


def test_disconnect_group() -> None:
    assert DisconnectCommand(integration_registry=None).meta.group == "Integrations"


@pytest.mark.asyncio
async def test_disconnect_no_args_returns_rendered_usage() -> None:
    class _Registry:
        def get(self, service: str) -> object:  # pragma: no cover — not reached
            raise AssertionError

    cmd = DisconnectCommand(integration_registry=_Registry())  # type: ignore[arg-type]
    result = await cmd.handle("", _state())
    assert result == render_usage("disconnect", cmd.meta)
    assert "[service]" in result
