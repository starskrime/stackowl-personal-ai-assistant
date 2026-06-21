"""/config sub-command metadata — mirrors test_audit_meta.py.

Asserts the declared metadata matches the real if/elif dispatch ladder and that
an empty or unknown sub-command surfaces the auto-generated usage block.
"""

from __future__ import annotations

import pytest

from stackowl.commands.config_command import ConfigCommand
from stackowl.commands.metadata import render_usage

_EXPECTED = {"list", "get", "set", "reset", "export"}


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


def test_config_declares_all_subcommands() -> None:
    cmd = ConfigCommand()
    names = {s.name for s in cmd.meta.subcommands}
    assert names == _EXPECTED


def test_config_grammar_is_verb() -> None:
    assert ConfigCommand().meta.grammar == "verb"
    assert ConfigCommand().meta.group == "Configuration"


def test_every_config_subcommand_has_nonempty_summary() -> None:
    cmd = ConfigCommand()
    for sub in cmd.meta.subcommands:
        assert sub.summary.strip(), f"/config {sub.name} has a blank summary"


@pytest.mark.asyncio
async def test_unknown_subcommand_returns_usage() -> None:
    """`/config bogus` shows the auto-generated usage with every sub listed."""
    cmd = ConfigCommand()
    out = await cmd.handle("bogus whatever", _state())
    assert out == render_usage("config", cmd.meta)
    for name in _EXPECTED:
        assert name in out


@pytest.mark.asyncio
async def test_empty_args_returns_usage() -> None:
    cmd = ConfigCommand()
    out = await cmd.handle("", _state())
    assert out == render_usage("config", cmd.meta)
