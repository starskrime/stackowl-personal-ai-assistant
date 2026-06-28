"""S6 — entry points: /agent and /owl both reach the owl/agent system, and the
first-contact discovery nudge is a usable one-liner.

Registration is asserted through the SAME single spine the product boots with
(register_all_commands(CommandDeps())), so a future dep-guarded regression that
drops either command turns this RED.
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


def test_c_agent_and_owl_entry_points_registered() -> None:
    """Both /agent and /owl register unconditionally via the assembly spine."""
    CommandRegistry.reset()
    register_all_commands(CommandDeps())
    live = {c.command for c in CommandRegistry.instance().list()}
    assert "agent" in live, "the /agent entry point must be reachable"
    assert "owl" in live, "the /owl entry point must be reachable"
    assert "owls" in live, "the /owls entry point must remain reachable"


def test_c_owl_alias_reaches_owl_surface() -> None:
    """/owl inherits the full owl surface (it IS an OwlsCommand)."""
    from stackowl.commands.owls_command import OwlCommand, OwlsCommand

    cmd = OwlCommand()
    assert cmd.command == "owl"
    assert isinstance(cmd, OwlsCommand)
    # the meta (subcommands) is inherited — same owl surface
    assert any(s.name == "add" for s in cmd.meta.subcommands)


def test_discovery_nudge_is_a_usable_one_liner() -> None:
    nudge = discovery_nudge()
    assert isinstance(nudge, str) and nudge.strip()
    assert "/owl" in nudge
    assert "\n" not in nudge, "the nudge must stay a single line (no spam)"
