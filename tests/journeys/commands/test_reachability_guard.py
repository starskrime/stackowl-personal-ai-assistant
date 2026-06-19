"""Reachability guard — every SHIPPED_COMMANDS entry must be reachable via the registry.

This is the permanent enforcement gate for "shipped ⟺ reachable". All 29
commands are wired (Epic B complete), so this is a hard CI gate. Because
registration is dep-INDEPENDENT (register_all_commands registers every command
even with empty deps), driving it with empty CommandDeps is a true proxy for
production reachability — a command that fails to register here would silently
become "Unknown slash command" in production. Set equality (==) catches BOTH a
dead command (shipped but unwired) and a stale one (wired but retired).

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


def test_every_shipped_command_is_reachable() -> None:
    """Registry must contain EXACTLY SHIPPED_COMMANDS — no more, no fewer."""
    CommandRegistry.reset()
    register_all_commands(CommandDeps())
    live = {c.command for c in CommandRegistry.instance().list()}
    assert live == SHIPPED_COMMANDS, (
        f"Registry mismatch.\n"
        f"  extra (live but not shipped): {live - SHIPPED_COMMANDS}\n"
        f"  missing (shipped but not live): {SHIPPED_COMMANDS - live}"
    )
