"""Story 4.2 — every live slash command AND sub-command declares whether it
changes state, and every state-changing entry names the future command
type(s) it will eventually submit through Epic 4's "one door" gate (FR76,
FR79).

Commands have no per-file severity mechanism (unlike tools' ``ToolManifest``),
so ``commands/state_census.py`` is the one central, reviewable declaration —
``COMMAND_CENSUS`` for the 33 top-level commands, ``SUBCOMMAND_CENSUS`` for the
91 dotted sub-command paths, recursively flattened from ``CommandMeta.
subcommands`` / ``SubCommand.children``.

RULE (mirrors ``tests/journeys/commands/test_reachability_guard.py``): this
file MUST drive the SAME real-registry construction — ``register_all_commands
(CommandDeps())`` — never a hand-built registry or bare ``cls()`` construction,
which would silently skip DI commands with required constructor args.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from stackowl.authz.state_change_census import COMMAND_TYPE_MIGRATIONS, MIGRATION_STORIES
from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.state_census import COMMAND_CENSUS, SUBCOMMAND_CENSUS


@pytest.fixture(autouse=True)
def _isolate_registry():  # type: ignore[no-untyped-def]
    """Snapshot+restore so this test never bleeds into the suite (same pattern
    as test_reachability_guard.py's fixture)."""
    snapshot = list(CommandRegistry.instance().list())
    yield
    CommandRegistry.reset()
    for cmd in snapshot:
        CommandRegistry.instance().register(cmd)


def _flatten_subcommand_paths(subs, prefix: str) -> list[str]:
    """Recursively flatten a SubCommand tree into dotted paths (e.g.
    'browser.profile.delete'), covering N levels the same way
    metadata.py::resolve_path does."""
    paths: list[str] = []
    for sub in subs:
        path = f"{prefix}.{sub.name}"
        paths.append(path)
        paths.extend(_flatten_subcommand_paths(sub.children, path))
    return paths


def _live_commands():
    CommandRegistry.reset()
    register_all_commands(CommandDeps())
    return CommandRegistry.instance().list()


def _live_subcommand_paths(commands) -> set[str]:
    paths: set[str] = set()
    for cmd in commands:
        paths.update(_flatten_subcommand_paths(cmd.meta.subcommands, cmd.command))
    return paths


@dataclass(frozen=True)
class _FakeDeclaration:
    """Minimal stand-in for CommandDeclaration, used only to prove the checker
    functions catch a bad input without mutating the real census."""

    name: str
    action_severity: str
    command_types: tuple[str, ...] = ()


def _declarations_missing_command_types(declarations) -> list[str]:
    return [d.name for d in declarations if d.action_severity != "read" and not d.command_types]


def _undeclared_command_types(declarations) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    for d in declarations:
        for ct in d.command_types:
            if ct not in COMMAND_TYPE_MIGRATIONS:
                missing.setdefault(ct, []).append(d.name)
    return missing


class TestTopLevelClosure:
    def test_command_census_matches_the_live_registry_exactly(self) -> None:
        live = {c.command for c in _live_commands()}
        census = set(COMMAND_CENSUS)
        assert live == census, (
            f"extra (live but undeclared): {live - census}\n"
            f"missing (declared but not live): {census - live}"
        )

    def test_command_census_matches_shipped_commands(self) -> None:
        """SHIPPED_COMMANDS is the reachability guard's own ground truth
        (commands/manifest.py) — the census must agree with it too."""
        from stackowl.commands.manifest import SHIPPED_COMMANDS

        assert set(COMMAND_CENSUS) == SHIPPED_COMMANDS


class TestSubcommandClosure:
    def test_subcommand_census_matches_the_live_flattened_tree_exactly(self) -> None:
        live = _live_subcommand_paths(_live_commands())
        census = set(SUBCOMMAND_CENSUS)
        assert live == census, (
            f"extra (live but undeclared): {live - census}\n"
            f"missing (declared but not live): {census - live}"
        )

    def test_population_is_real(self) -> None:
        """VACUITY CONTROL — a floor so a wiring regression can't pass by
        measuring an empty set."""
        assert len(COMMAND_CENSUS) >= 30
        assert len(SUBCOMMAND_CENSUS) >= 80


class TestEveryNonReadEntryNamesACommandType:
    def test_every_non_read_top_level_command_has_nonempty_command_types(self) -> None:
        offenders = [
            name
            for name, decl in COMMAND_CENSUS.items()
            if decl.action_severity != "read" and not decl.command_types
        ]
        assert not offenders, offenders

    def test_every_non_read_subcommand_has_nonempty_command_types(self) -> None:
        offenders = [
            path
            for path, decl in SUBCOMMAND_CENSUS.items()
            if decl.action_severity != "read" and not decl.command_types
        ]
        assert not offenders, offenders

    def test_read_entries_declare_no_command_types(self) -> None:
        offenders = [
            name
            for name, decl in {**COMMAND_CENSUS, **SUBCOMMAND_CENSUS}.items()
            if decl.action_severity == "read" and decl.command_types
        ]
        assert not offenders, offenders

    def test_checker_catches_an_injected_undeclared_entry(self) -> None:
        """Self-check (red/green proof, AC2)."""
        fake = _FakeDeclaration(name="throwaway.probe", action_severity="write")
        assert _declarations_missing_command_types([fake]) == ["throwaway.probe"]

    def test_checker_passes_a_correctly_declared_entry(self) -> None:
        fake = _FakeDeclaration(
            name="throwaway.probe", action_severity="write", command_types=("x.y",)
        )
        assert _declarations_missing_command_types([fake]) == []


class TestEveryDeclaredCommandTypeIsLedgered:
    def test_every_top_level_command_type_has_a_ledger_entry(self) -> None:
        missing = _undeclared_command_types(
            _FakeDeclaration(name, decl.action_severity, decl.command_types)
            for name, decl in COMMAND_CENSUS.items()
        )
        assert not missing, missing

    def test_every_subcommand_command_type_has_a_ledger_entry(self) -> None:
        missing = _undeclared_command_types(
            _FakeDeclaration(path, decl.action_severity, decl.command_types)
            for path, decl in SUBCOMMAND_CENSUS.items()
        )
        assert not missing, missing

    def test_every_ledgered_command_type_names_a_real_story(self) -> None:
        offenders = {}
        for _name, decl in {**COMMAND_CENSUS, **SUBCOMMAND_CENSUS}.items():
            for ct in decl.command_types:
                entry = COMMAND_TYPE_MIGRATIONS.get(ct)
                if entry is not None and entry.story not in MIGRATION_STORIES:
                    offenders[ct] = entry.story
        assert not offenders, offenders

    def test_checker_catches_an_injected_undeclared_command_type(self) -> None:
        """Self-check (red/green proof)."""
        fake = _FakeDeclaration(
            name="throwaway.probe",
            action_severity="write",
            command_types=("throwaway.undeclared_probe",),
        )
        assert _undeclared_command_types([fake]) == {
            "throwaway.undeclared_probe": ["throwaway.probe"]
        }
