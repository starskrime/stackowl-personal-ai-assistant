"""/agent sub-command metadata — declared subs match the dispatch ladder.

Mirrors tests/commands/test_audit_meta.py for the migrated /agent command.
"""

from __future__ import annotations

import pytest

from stackowl.commands.agent_create_command import AgentCommand

_EXPECTED = {
    "create",
    "confirm",
    "cancel",
    "list",
    "log",
    "pause",
    "resume",
    "acknowledge",
    "stop",
}


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


def test_agent_declares_all_subcommands() -> None:
    cmd = AgentCommand()
    names = {s.name for s in cmd.meta.subcommands}
    assert names == _EXPECTED


def test_agent_meta_is_verb_grammar_and_grouped() -> None:
    cmd = AgentCommand()
    assert cmd.meta.grammar == "verb"
    assert cmd.meta.group == "Agents & Automation"


def test_agent_every_subcommand_has_summary() -> None:
    cmd = AgentCommand()
    for sub in cmd.meta.subcommands:
        assert sub.summary.strip()
        assert not sub.summary.endswith(".")


@pytest.mark.asyncio
async def test_unknown_subcommand_returns_usage() -> None:
    """`/agent bogus` shows the auto-generated usage, not a crash."""
    cmd = AgentCommand()
    out = await cmd.handle("bogus", _state())
    assert "Usage: /agent" in out
    for name in _EXPECTED:
        assert name in out


@pytest.mark.asyncio
async def test_bare_agent_returns_usage() -> None:
    """A bare `/agent` (no sub) renders usage."""
    cmd = AgentCommand()
    out = await cmd.handle("", _state())
    assert "Usage: /agent" in out
