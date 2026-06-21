"""/staged sub-command metadata — declared subs match the dispatch ladder.

Mirrors tests/commands/test_audit_meta.py for the migrated /staged command.
"""

from __future__ import annotations

import pytest

from stackowl.commands.staged_command import StagedCommand

_EXPECTED = {"list", "review", "reject", "promote"}


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


def _cmd() -> StagedCommand:
    return StagedCommand(bridge=_FakeBridge(), promoter=None)


def test_staged_declares_all_subcommands() -> None:
    cmd = _cmd()
    names = {s.name for s in cmd.meta.subcommands}
    assert names == _EXPECTED


def test_staged_meta_is_verb_grammar_and_grouped() -> None:
    cmd = _cmd()
    assert cmd.meta.grammar == "verb"
    assert cmd.meta.group == "Memory & Knowledge"


def test_staged_every_subcommand_has_summary() -> None:
    cmd = _cmd()
    for sub in cmd.meta.subcommands:
        assert sub.summary.strip()
        assert not sub.summary.endswith(".")


@pytest.mark.asyncio
async def test_unknown_subcommand_returns_usage() -> None:
    """`/staged bogus` shows the auto-generated usage."""
    cmd = _cmd()
    out = await cmd.handle("bogus", _state())
    assert "Usage: /staged" in out
    for name in _EXPECTED:
        assert name in out


@pytest.mark.asyncio
async def test_bare_staged_returns_usage() -> None:
    """A bare `/staged` (no sub) renders usage."""
    cmd = _cmd()
    out = await cmd.handle("", _state())
    assert "Usage: /staged" in out


class _FakeBridge:
    """Minimal stand-in so handle() passes the `bridge is None` guard."""

    async def list_staged(self, status=None):  # type: ignore[no-untyped-def]
        return []
