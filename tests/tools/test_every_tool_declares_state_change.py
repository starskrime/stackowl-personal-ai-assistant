"""Story 4.2 — every live tool declares whether it changes state, and every
state-changing tool names the future command type(s) it will eventually submit
through Epic 4's "one door" gate (FR76, FR79).

Structural enforcement lives on ``ToolManifest`` itself (``tools/base.py``): its
field default was removed and a model-validator requires ``command_types`` be
non-empty whenever ``action_severity != "read"``. This file is the CLOSURE
tripwire on top of that — it proves the enforced tree is the whole live tree
(``ToolRegistry.with_defaults().all()``, never a hand-picked subset), names any
offender explicitly (belt-and-suspenders over the Pydantic validator), and
cross-checks every declared command type against the shared ledger
(``authz/state_change_census.py``).

EXEMPTION, NOTED RATHER THAN SILENTLY SKIPPED (spec-4-2 Boundaries): dynamic,
per-install, model-authored ``LearnedShellTool`` instances are not classified by
this story. ``LearnedToolSpec.action_severity`` (``tools/meta/tool_spec.py``)
already REQUIRES a value with a safe ``"consequential"`` default, and the class
is already excluded from static discovery via ``tools/_infra/discovery.py::
EXCLUDED_FROM_DISCOVERY`` — so ``ToolRegistry.with_defaults().all()`` never
surfaces one, and this test's population is exactly the static catalog.
"""

from __future__ import annotations

from dataclasses import dataclass

from stackowl.authz.state_change_census import COMMAND_TYPE_MIGRATIONS, MIGRATION_STORIES
from stackowl.tools._infra.discovery import EXCLUDED_FROM_DISCOVERY
from stackowl.tools.meta.tool_spec import LearnedToolSpec
from stackowl.tools.registry import ToolRegistry


def _live_tools():
    return ToolRegistry.with_defaults().all()


@dataclass(frozen=True)
class _FakeManifest:
    """A minimal stand-in with just the two fields the checkers below read —
    used ONLY to prove the checker functions actually catch a bad input,
    without mutating the real registry."""

    name: str
    action_severity: str
    command_types: tuple[str, ...] = ()


def _tools_missing_command_types(manifests) -> list[str]:
    """Every non-read manifest with empty command_types, by name."""
    return [m.name for m in manifests if m.action_severity != "read" and not m.command_types]


def _undeclared_command_types(manifests) -> dict[str, list[str]]:
    """Every tool-declared command type absent from the shared ledger, mapped
    to the tool name(s) that declared it."""
    missing: dict[str, list[str]] = {}
    for m in manifests:
        for ct in m.command_types:
            if ct not in COMMAND_TYPE_MIGRATIONS:
                missing.setdefault(ct, []).append(m.name)
    return missing


class TestPopulationIsReal:
    """VACUITY CONTROL — a floor on the population so a wiring regression that
    silently shrank the catalog can't make the closure checks below pass by
    measuring an empty (or near-empty) set."""

    def test_at_least_77_live_tools(self) -> None:
        tools = _live_tools()
        assert len(tools) >= 77, f"only {len(tools)} live tools — expected >= 77"

    def test_at_least_37_are_non_read(self) -> None:
        nonread = [t for t in _live_tools() if t.manifest.action_severity != "read"]
        assert len(nonread) >= 37, f"only {len(nonread)} non-read tools — expected >= 37"

    def test_learned_shell_tool_is_exempt_and_excluded_from_this_population(self) -> None:
        """The exemption this file's docstring claims, proven two ways: the
        exclusion set names the class, AND its own manifest field is already
        structurally required with a safe default (tools/meta/tool_spec.py) —
        so even if the exclusion ever lapsed, it would not silently default to
        "read"."""
        assert "LearnedShellTool" in EXCLUDED_FROM_DISCOVERY
        assert "action_severity" in LearnedToolSpec.model_fields
        assert LearnedToolSpec.model_fields["action_severity"].is_required() is False
        assert LearnedToolSpec.model_fields["action_severity"].default == "consequential"
        assert not any(t.name == "LearnedShellTool" for t in _live_tools())


class TestEveryNonReadToolNamesACommandType:
    """Belt-and-suspenders over ToolManifest's own model-validator: even if that
    validator were ever weakened, this tripwire independently re-checks the
    live tree and NAMES the offender."""

    def test_every_non_read_tool_has_nonempty_command_types(self) -> None:
        offenders = _tools_missing_command_types(t.manifest for t in _live_tools())
        assert not offenders, (
            "these tools are write/consequential with no command_types declared: "
            f"{offenders}"
        )

    def test_read_tools_declare_no_command_types(self) -> None:
        """Sanity: a read tool has nothing pending to migrate."""
        offenders = [
            t.name
            for t in _live_tools()
            if t.manifest.action_severity == "read" and t.manifest.command_types
        ]
        assert not offenders, (
            f"these tools are 'read' but declare command_types anyway: {offenders}"
        )

    def test_checker_catches_an_injected_undeclared_tool(self) -> None:
        """Self-check (red/green proof, AC2): a synthetic tool with
        action_severity='write' and no command_types is actually caught by the
        same checker the live-tree test above uses, not merely absent from the
        real tree by accident."""
        fake = _FakeManifest(name="throwaway_probe_tool", action_severity="write")
        assert _tools_missing_command_types([fake]) == ["throwaway_probe_tool"]

    def test_checker_passes_a_correctly_declared_tool(self) -> None:
        fake = _FakeManifest(
            name="throwaway_probe_tool", action_severity="write", command_types=("x.y",)
        )
        assert _tools_missing_command_types([fake]) == []


class TestEveryDeclaredCommandTypeIsLedgered:
    """Forward-direction cross-check: nothing a live tool declares is undeclared
    in the shared ledger (authz/state_change_census.py)."""

    def test_every_tool_command_type_has_a_ledger_entry(self) -> None:
        missing = _undeclared_command_types(t.manifest for t in _live_tools())
        assert not missing, (
            "these tool-declared command types have no COMMAND_TYPE_MIGRATIONS "
            f"entry: {missing}"
        )

    def test_every_ledgered_tool_command_type_names_a_real_story(self) -> None:
        offenders = {
            ct: entry.story
            for t in _live_tools()
            for ct in t.manifest.command_types
            if (entry := COMMAND_TYPE_MIGRATIONS.get(ct)) is not None
            and entry.story not in MIGRATION_STORIES
        }
        assert not offenders, f"ledger entries with an unrecognized story: {offenders}"

    def test_checker_catches_an_injected_undeclared_command_type(self) -> None:
        """Self-check (red/green proof): a synthetic tool naming a command type
        absent from the ledger is actually caught."""
        fake = _FakeManifest(
            name="throwaway_probe_tool",
            action_severity="write",
            command_types=("throwaway.undeclared_probe",),
        )
        assert _undeclared_command_types([fake]) == {
            "throwaway.undeclared_probe": ["throwaway_probe_tool"]
        }
