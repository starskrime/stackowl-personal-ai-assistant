"""/owls sub-command metadata — declared subs match the dispatch ladder.

Mirrors tests/commands/test_audit_meta.py for the migrated /owls command.
"""

from __future__ import annotations

import pytest

from stackowl.commands.owls_command import OwlsCommand

_EXPECTED = {
    "list", "create", "edit", "remove", "health", "dna", "reset-dna",
    "objectives", "objective", "objective-cancel",
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


def test_owls_declares_all_subcommands() -> None:
    cmd = OwlsCommand()
    names = {s.name for s in cmd.meta.subcommands}
    assert names == _EXPECTED


def test_owls_meta_is_verb_grammar_and_grouped() -> None:
    cmd = OwlsCommand()
    assert cmd.meta.grammar == "verb"
    assert cmd.meta.group == "Owls"


def test_owls_every_subcommand_has_summary() -> None:
    cmd = OwlsCommand()
    for sub in cmd.meta.subcommands:
        assert sub.summary.strip()
        assert not sub.summary.endswith(".")


@pytest.mark.asyncio
async def test_unknown_subcommand_returns_usage() -> None:
    """`/owls bogus` shows the auto-generated usage, not _NO_REGISTRY."""
    cmd = OwlsCommand()
    out = await cmd.handle("bogus", _state())
    assert "Usage: /owls" in out
    for name in _EXPECTED:
        assert name in out
