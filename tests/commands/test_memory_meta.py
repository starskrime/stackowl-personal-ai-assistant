"""/memory sub-command metadata — mirrors test_audit_meta.py.

Asserts the declared metadata matches the real if/elif dispatch ladder and that
an unknown sub-command surfaces the auto-generated usage block (not silent).
"""

from __future__ import annotations

import pytest

from stackowl.commands.memory_command import MemoryCommand
from stackowl.commands.metadata import render_usage

_EXPECTED = {
    "stats",
    "search",
    "delete",
    "budget",
    "reindex",
    "remember",
    "forget",
    "export",
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


def test_memory_declares_all_subcommands() -> None:
    cmd = MemoryCommand()
    names = {s.name for s in cmd.meta.subcommands}
    assert names == _EXPECTED


def test_every_memory_subcommand_has_nonempty_summary() -> None:
    cmd = MemoryCommand()
    for sub in cmd.meta.subcommands:
        assert sub.summary.strip(), f"/memory {sub.name} has a blank summary"


@pytest.mark.asyncio
async def test_unknown_subcommand_returns_usage() -> None:
    """`/memory bogus` shows the auto-generated usage with every sub listed."""
    cmd = MemoryCommand(
        bridge=object(),  # type: ignore[arg-type]
        settings=object(),  # type: ignore[arg-type]
        db=object(),  # type: ignore[arg-type]
        event_bus=object(),  # type: ignore[arg-type]
    )
    out = await cmd.handle("bogus whatever", _state())
    assert out == render_usage("memory", cmd.meta)
    for name in _EXPECTED:
        assert name in out


@pytest.mark.asyncio
async def test_empty_args_returns_usage() -> None:
    cmd = MemoryCommand(
        bridge=object(),  # type: ignore[arg-type]
        settings=object(),  # type: ignore[arg-type]
        db=object(),  # type: ignore[arg-type]
        event_bus=object(),  # type: ignore[arg-type]
    )
    out = await cmd.handle("", _state())
    assert out == render_usage("memory", cmd.meta)
