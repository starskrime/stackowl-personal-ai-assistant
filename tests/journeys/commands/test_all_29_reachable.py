"""Positive reachability test — every shipped command registers (Epic B campaign goal).

This test is NOT marked xfail.  It proves the reachability campaign's goal is met:
after all Epic B wiring commits, register_all_commands(CommandDeps()) produces
exactly the SHIPPED_COMMANDS set.

The xfail burndown guard in test_reachability_guard.py still carries the marker;
its removal is a separate final commit.  This test is the hard positive gate that
runs alongside the guard and will turn RED immediately if any command is later
accidentally unwired.
"""

from __future__ import annotations

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.manifest import SHIPPED_COMMANDS
from stackowl.commands.registry import CommandRegistry
import pytest


@pytest.fixture(autouse=True)
def _isolate_registry():  # type: ignore[no-untyped-def]
    """Snapshot+restore so this test never bleeds into the suite."""
    snapshot = list(CommandRegistry.instance().list())
    yield
    CommandRegistry.reset()
    for cmd in snapshot:
        CommandRegistry.instance().register(cmd)


def test_all_shipped_commands_register() -> None:
    """register_all_commands(CommandDeps()) must produce exactly SHIPPED_COMMANDS.

    This is the positive gate that proves Epic B's reachability goal is fully met.
    If any command is accidentally unwired in a future commit, this test turns RED.
    """
    CommandRegistry.reset()
    register_all_commands(CommandDeps())
    live = {c.command for c in CommandRegistry.instance().list()}

    missing = SHIPPED_COMMANDS - live
    extra = live - SHIPPED_COMMANDS

    assert not missing, (
        f"Commands in SHIPPED_COMMANDS but NOT registered ({len(missing)}): {sorted(missing)}"
    )
    assert not extra, (
        f"Commands registered but NOT in SHIPPED_COMMANDS ({len(extra)}): {sorted(extra)}"
    )
    assert len(live) == len(SHIPPED_COMMANDS), (
        f"Count mismatch: registered={len(live)}, shipped={len(SHIPPED_COMMANDS)}"
    )
