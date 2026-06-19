"""Reachability guard — every SHIPPED_COMMANDS entry must be reachable via the registry.

This test is the enforcement gate for the Epic A → Epic B burndown.
It is marked xfail(strict=True) because only 15 of 29 commands are wired today.
When Epic B finishes wiring all 29, remove the xfail marker and the test becomes
a hard CI gate: if it unexpectedly passes early (strict=True), CI fails — that
is intentional behaviour so we don't silently ship a "95% complete" registry.

RULE: this test MUST call register_all_commands — never hand-build a registry
or construct command objects directly in this file.
"""

from __future__ import annotations

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.manifest import SHIPPED_COMMANDS
from stackowl.commands.registry import CommandRegistry


@pytest.fixture(autouse=True)
def _isolate_registry():  # type: ignore[no-untyped-def]
    """Snapshot+restore so this test never bleeds into the suite."""
    snapshot = list(CommandRegistry.instance().list())
    yield
    CommandRegistry.reset()
    for cmd in snapshot:
        CommandRegistry.instance().register(cmd)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "burndown: slash-command-overhaul Epic B wires the remaining commands; "
        "remove this marker in the final Epic B wiring commit"
    ),
)
def test_every_shipped_command_is_reachable() -> None:
    """Registry must contain EXACTLY SHIPPED_COMMANDS — no more, no fewer.

    Today only 15 of 29 are wired so this assertion fails (xfail).
    When all 29 are wired, remove the xfail marker.
    """
    CommandRegistry.reset()
    register_all_commands(CommandDeps())
    live = {c.command for c in CommandRegistry.instance().list()}
    assert live == SHIPPED_COMMANDS, (
        f"Registry mismatch.\n"
        f"  extra (live but not shipped): {live - SHIPPED_COMMANDS}\n"
        f"  missing (shipped but not live): {SHIPPED_COMMANDS - live}"
    )
