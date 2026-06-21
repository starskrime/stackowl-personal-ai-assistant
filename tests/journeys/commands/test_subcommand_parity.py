"""Sub-command parity contract — declared metadata must match real dispatch.

This is the second drift seam (the first being SHIPPED_COMMANDS == registry).
For every command that declares sub-command metadata, the contract enforces:

1.  **Declared ⟹ routed** (execution probe): every declared sub-command, when
    invoked, does NOT fall through to the command's auto-usage / unknown-sub
    branch.  Catches "declared but the handler `else`-branches it".

2.  **No silent gaps in metadata content**: every declared node has a non-empty
    ``summary`` (so autocomplete and /help never render a blank row), and a
    ``verb``-grammar command's sub-command names are unique among siblings
    (recursively).

The probe drives the REAL registered command via register_all_commands — never
a hand-built registry — so the two sides (metadata vs dispatch) can genuinely
disagree.  A guard that cannot go red is a lie that looks like a test.
"""

from __future__ import annotations

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.metadata import SubCommand
from stackowl.commands.registry import CommandRegistry


@pytest.fixture(autouse=True)
def _isolate_registry():  # type: ignore[no-untyped-def]
    snapshot = list(CommandRegistry.instance().list())
    yield
    CommandRegistry.reset()
    for cmd in snapshot:
        CommandRegistry.instance().register(cmd)


def _all_nodes(subs: tuple[SubCommand, ...]) -> list[SubCommand]:
    out: list[SubCommand] = []
    for s in subs:
        out.append(s)
        out.extend(_all_nodes(s.children))
    return out


def test_declared_subcommands_have_nonempty_summary() -> None:
    CommandRegistry.reset()
    register_all_commands(CommandDeps())
    offenders: list[str] = []
    for cmd in CommandRegistry.instance().list():
        for node in _all_nodes(cmd.meta.subcommands):
            if not node.summary.strip():
                offenders.append(f"/{cmd.command} {node.name}")
    assert not offenders, f"sub-commands with blank summary: {offenders}"


def test_sibling_subcommand_names_are_unique() -> None:
    CommandRegistry.reset()
    register_all_commands(CommandDeps())

    def check(prefix: str, subs: tuple[SubCommand, ...], collisions: list[str]) -> None:
        seen: set[str] = set()
        for s in subs:
            key = s.name.casefold()
            if key in seen:
                collisions.append(f"{prefix} {s.name}")
            seen.add(key)
            check(f"{prefix} {s.name}", s.children, collisions)

    collisions: list[str] = []
    for cmd in CommandRegistry.instance().list():
        check(f"/{cmd.command}", cmd.meta.subcommands, collisions)
    assert not collisions, f"duplicate sibling sub-commands: {collisions}"


def test_verb_grammar_implies_declared_subcommands() -> None:
    """A command may only advertise selectable subs when grammar == 'verb'.

    flag/leaf commands must NOT carry subcommands (that would lie to the
    autocomplete dropdown).
    """
    CommandRegistry.reset()
    register_all_commands(CommandDeps())
    liars: list[str] = []
    for cmd in CommandRegistry.instance().list():
        if cmd.meta.grammar != "verb" and cmd.meta.subcommands:
            liars.append(f"/{cmd.command} (grammar={cmd.meta.grammar})")
    assert not liars, f"non-verb commands declaring subcommands: {liars}"


@pytest.mark.asyncio
async def test_declared_subcommands_do_not_hit_unknown_branch() -> None:
    """Execution probe: every declared top-level sub-command routes to real work.

    We invoke `/cmd <sub>` and assert the result is NOT the auto-usage block
    for that command — i.e. the handler recognised the sub-command rather than
    `else`-branching it to usage. Commands whose subs need side-effecting args
    are exercised lightly; we only assert the unknown-sub usage marker is absent.
    """
    from stackowl.commands.metadata import render_usage
    from stackowl.pipeline.state import PipelineState

    CommandRegistry.reset()
    register_all_commands(CommandDeps())
    registry = CommandRegistry.instance()

    misrouted: list[str] = []
    for cmd in registry.list():
        if cmd.meta.grammar != "verb" or not cmd.meta.subcommands:
            continue
        usage = render_usage(cmd.command, cmd.meta)
        for sub in cmd.meta.subcommands:
            state = PipelineState(
                trace_id="trace-1",
                session_id="parity-probe",
                input_text="",
                channel="cli",
                owl_name="Daria",
                pipeline_step="receive",
            )
            try:
                # Drive the REAL dispatch path (registry + assembler), never
                # a direct handle() call — that is the production route.
                out = await registry.dispatch(cmd.command, sub.name, state)
            except Exception:
                # A handler that *tried* to act (and raised on missing args /
                # unconfigured deps) is still routing the sub-command, not
                # else-branching it. That is acceptable for this probe.
                continue
            if out == usage:
                misrouted.append(f"/{cmd.command} {sub.name}")
    assert not misrouted, (
        "declared sub-commands that fell through to auto-usage "
        f"(declared but not routed): {misrouted}"
    )
