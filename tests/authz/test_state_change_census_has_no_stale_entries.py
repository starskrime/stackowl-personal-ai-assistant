"""Story 4.2 — the REVERSE direction of the closure check: every entry in the
shared ledger (``authz/state_change_census.py::COMMAND_TYPE_MIGRATIONS``) must
be declared by at least one live tool or command/sub-command.

WHY THIS LIVES HERE, SEPARATE FROM THE TOOL/COMMAND TRIPWIRES. Neither
``tests/tools/test_every_tool_declares_state_change.py`` nor
``tests/commands/test_every_command_declares_state_change.py`` can make this
check alone — each only sees its own half of the union (tools ∪ commands). A
ledger entry that names a command type NEITHER side declares anymore (e.g. a
tool was retired, or a command type was renamed and the old key left behind)
is a stale entry: the exact "declared once, never revisited" drift
``journal/coverage.py``'s own docstring names as the reason ``UNJOURNALED_
TABLES`` is a dict a person edits, not a query result.

This is a ONE-WAY check. The forward direction (nothing a live tool/command
declares is missing from the ledger) is proven in the two files above; this
file only proves the ledger doesn't outlive what declares it.
"""

from __future__ import annotations

import pytest

from stackowl.authz.state_change_census import COMMAND_TYPE_MIGRATIONS
from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.state_census import COMMAND_CENSUS, SUBCOMMAND_CENSUS
from stackowl.tools.registry import ToolRegistry

pytestmark = pytest.mark.tripwire


@pytest.fixture(autouse=True)
def _isolate_registry():  # type: ignore[no-untyped-def]
    """Snapshot+restore so registering commands here never bleeds into the
    suite (same pattern as test_reachability_guard.py's fixture)."""
    snapshot = list(CommandRegistry.instance().list())
    yield
    CommandRegistry.reset()
    for cmd in snapshot:
        CommandRegistry.instance().register(cmd)


def _all_declared_command_types() -> frozenset[str]:
    """Every command type named anywhere live — tools' ToolManifest.command_types
    union commands'/sub-commands' CommandDeclaration.command_types."""
    tool_types = {
        ct
        for t in ToolRegistry.with_defaults().all()
        for ct in t.manifest.command_types
    }
    command_types = {
        ct
        for decl in COMMAND_CENSUS.values()
        for ct in decl.command_types
    }
    subcommand_types = {
        ct
        for decl in SUBCOMMAND_CENSUS.values()
        for ct in decl.command_types
    }
    return frozenset(tool_types | command_types | subcommand_types)


def stale_ledger_entries(declared: frozenset[str]) -> frozenset[str]:
    """Every COMMAND_TYPE_MIGRATIONS key no live tool or command declares.

    An empty result is the tripwire passing. A non-empty one names exactly the
    orphaned keys — AC5's "stale-entry tripwire fails, names the orphaned key".
    """
    return frozenset(COMMAND_TYPE_MIGRATIONS) - declared


class TestNoStaleLedgerEntries:
    def test_registry_construction_is_the_real_one(self) -> None:
        """RULE (mirrors test_reachability_guard.py): drive the SAME real
        register_all_commands(CommandDeps()) construction — never bare cls()."""
        CommandRegistry.reset()
        reg = register_all_commands(CommandDeps())
        assert len(reg.list()) >= 30

    def test_every_ledger_entry_is_declared_by_a_live_tool_or_command(self) -> None:
        declared = _all_declared_command_types()
        stale = stale_ledger_entries(declared)
        assert not stale, (
            "these COMMAND_TYPE_MIGRATIONS entries name a command type no live "
            f"tool or command declares anymore (stale): {sorted(stale)}"
        )

    def test_the_ledger_is_not_empty(self) -> None:
        """VACUITY CONTROL — an empty ledger would make the check above pass
        trivially by having nothing to be stale."""
        assert len(COMMAND_TYPE_MIGRATIONS) >= 40

    def test_detector_flags_an_injected_stale_entry(self) -> None:
        """Self-check (red/green proof): a ledger key nothing declares is
        actually caught by stale_ledger_entries(), not merely absent from the
        real ledger by accident."""
        declared = _all_declared_command_types()
        assert "throwaway.orphaned_key" not in COMMAND_TYPE_MIGRATIONS
        assert "throwaway.orphaned_key" not in declared

        from stackowl.authz.state_change_census import CommandTypeMigration

        synthetic_ledger = dict(COMMAND_TYPE_MIGRATIONS)
        synthetic_ledger["throwaway.orphaned_key"] = CommandTypeMigration("4.10")

        stale = frozenset(synthetic_ledger) - declared
        assert "throwaway.orphaned_key" in stale
