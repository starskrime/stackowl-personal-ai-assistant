"""/skill sub-command metadata — mirrors test_audit_meta.py.

Asserts the declared metadata matches the real if/elif dispatch ladder and that
an unknown sub-command surfaces the auto-generated usage block (not silent).
"""

from __future__ import annotations

import pytest

from stackowl.commands.metadata import render_usage
from stackowl.commands.skill_command import SkillCommand

_EXPECTED = {
    "list",
    "show",
    "add",
    "rm",
    "edit",
    "diff",
    "enable",
    "disable",
    "reload",
    "restore",
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


def test_skill_declares_all_subcommands() -> None:
    cmd = SkillCommand()
    names = {s.name for s in cmd.meta.subcommands}
    assert names == _EXPECTED


def test_every_skill_subcommand_has_nonempty_summary() -> None:
    cmd = SkillCommand()
    for sub in cmd.meta.subcommands:
        assert sub.summary.strip(), f"/skill {sub.name} has a blank summary"


@pytest.mark.asyncio
async def test_unknown_subcommand_returns_usage() -> None:
    """`/skill bogus` shows the auto-generated usage with every sub listed."""
    from pathlib import Path

    cmd = SkillCommand(
        store=object(),  # type: ignore[arg-type]
        loader=object(),  # type: ignore[arg-type]
        skills_root=Path("/tmp"),
    )
    out = await cmd.handle("bogus whatever", _state())
    assert out == render_usage("skill", cmd.meta)
    for name in _EXPECTED:
        assert name in out


@pytest.mark.asyncio
async def test_empty_args_returns_usage() -> None:
    from pathlib import Path

    cmd = SkillCommand(
        store=object(),  # type: ignore[arg-type]
        loader=object(),  # type: ignore[arg-type]
        skills_root=Path("/tmp"),
    )
    out = await cmd.handle("", _state())
    assert out == render_usage("skill", cmd.meta)
