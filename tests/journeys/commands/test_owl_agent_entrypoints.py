"""S6 — entry points: /owl is the ONE owl surface (legacy /owls + /agent are
retired — see Task 7), and the first-contact discovery nudge is a usable
one-liner.

Registration is asserted through the SAME single spine the product boots with
(register_all_commands(CommandDeps())), so a future dep-guarded regression that
drops /owl, or resurrects /owls or /agent, turns this RED.
"""

from __future__ import annotations

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from stackowl.owls.discovery import discovery_nudge


@pytest.fixture(autouse=True)
def _isolate_registry():  # type: ignore[no-untyped-def]
    snapshot = list(CommandRegistry.instance().list())
    yield
    CommandRegistry.reset()
    for cmd in snapshot:
        CommandRegistry.instance().register(cmd)


def test_owl_entry_point_registered() -> None:
    """/owl registers unconditionally via the assembly spine and is the ONE owl
    surface (legacy /owls + /agent are gone — see Task 7)."""
    CommandRegistry.reset()
    register_all_commands(CommandDeps())
    live = {c.command for c in CommandRegistry.instance().list()}
    assert "owl" in live, "the /owl entry point must be reachable"
    assert "owls" not in live, "legacy /owls must be removed"
    assert "agent" not in live, "legacy /agent must be removed"


def test_owl_exposes_unified_surface() -> None:
    from stackowl.commands.owls_command import OwlCommand

    cmd = OwlCommand()
    assert cmd.command == "owl"
    names = {s.name for s in cmd.meta.subcommands}
    assert {"create", "pause", "resume", "retire"} <= names


def test_discovery_nudge_is_a_usable_one_liner() -> None:
    nudge = discovery_nudge()
    assert isinstance(nudge, str) and nudge.strip()
    assert "/owl" in nudge
    assert "\n" not in nudge, "the nudge must stay a single line (no spam)"
