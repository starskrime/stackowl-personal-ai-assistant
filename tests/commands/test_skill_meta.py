"""/skill sub-command metadata — mirrors test_audit_meta.py.

Asserts the declared metadata matches the real if/elif dispatch ladder and that
an unknown sub-command surfaces the auto-generated usage block (not silent).
"""

from __future__ import annotations

import pytest

from stackowl.commands.metadata import render_usage
from stackowl.commands.skill_command import SkillCommand

_EXPECTED = {
    # D10.5 — the one verb that USES a skill rather than managing it.
    "use",
    # D10.7 (2026-09-04) — the INVERSE of the `menu` case below. `pinned` had
    # three readers and no writer: the curator honoured it, `dedupe`'s own help
    # said "a pinned member wins outright", and nothing could set it. Declared
    # and dispatched together, so neither half can exist without the other.
    "pin",
    "unpin",
    # Found UNDECLARED on 2026-08-29: it dispatched at skill_command.py's
    # `elif sub == "menu"` and worked, but was absent from the meta, so /help,
    # /find and the CommandResolver corpus could not see it. Declared then.
    "menu",
    # D09.3 slice 4 / D10.2 slice 7 — the two catalog-maintenance passes.
    "dedupe",
    "migrate",
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
        session_key="s",
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


def test_every_dispatched_subcommand_is_DECLARED() -> None:
    """The lock-step guard this file's docstring always claimed to be.

    `_EXPECTED` is a hand-written set, so it pins the DECLARATION against itself and
    cannot notice a verb that dispatches without being declared. That is not
    hypothetical: `menu` did exactly that until 2026-08-29 — it worked, and /help,
    /find and the resolver corpus could not see it.

    So derive the truth from the dispatch ladder instead of asserting it. Two copies
    of one rule become one source and one reader, which is the shape this codebase's
    third failure mode is about.
    """
    import inspect
    import re

    src = inspect.getsource(SkillCommand.handle)
    dispatched = set(re.findall(r'sub == "([a-z]+)"', src))
    assert dispatched, "the ladder could not be read — this guard would pass vacuously"

    declared = {s.name for s in SkillCommand().meta.subcommands}
    undeclared = dispatched - declared
    assert not undeclared, (
        f"these verbs dispatch but are not declared, so /help and /find cannot see "
        f"them: {sorted(undeclared)}"
    )
