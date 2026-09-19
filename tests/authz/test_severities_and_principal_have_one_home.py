"""Story 4.1 — severities and the principal live in `authz/`, not `control_plane`.

WHY THIS EXISTS. `READ`, `WRITE`, `CONSEQUENTIAL`, `ALL_SEVERITIES` and
`ControlPrincipal` used to be DEFINED in `control_plane/auth.py`. Epic 4's "one
door" command gate (AD-1, AD-27) needs one shared severity/principal home in
`authz/` that survives `control_plane`'s later deletion (AD-7), so the five
symbols moved to `stackowl.authz.severity` and `control_plane/auth.py` now only
RE-EXPORTS them.

Two things keep that honest:

* An identity test — not just an equality test — proving the re-export really
  is the same object, not a second definition that happens to look alike.
* A `pytest.mark.tripwire` AST scan (modelled on
  `tests/tenancy/test_no_owner_scope_bypass.py`'s repo-wide scan style) that
  fails the build the moment any module outside `authz/` defines its own
  severity-shaped constant or its own `.may()` method — the exact shape a
  second, drifting definition would take.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from stackowl.authz import ALL_SEVERITIES as AUTHZ_ALL_SEVERITIES
from stackowl.authz import CONSEQUENTIAL as AUTHZ_CONSEQUENTIAL
from stackowl.authz import READ as AUTHZ_READ
from stackowl.authz import WRITE as AUTHZ_WRITE
from stackowl.authz import ControlPrincipal as AuthzControlPrincipal
from stackowl.control_plane.auth import ALL_SEVERITIES as CP_ALL_SEVERITIES
from stackowl.control_plane.auth import CONSEQUENTIAL as CP_CONSEQUENTIAL
from stackowl.control_plane.auth import READ as CP_READ
from stackowl.control_plane.auth import WRITE as CP_WRITE
from stackowl.control_plane.auth import ControlPrincipal as CPControlPrincipal

# CROSS-CUTTING GUARD — see tests/tenancy/test_no_owner_scope_bypass.py for why
# this marker matters: a property of the WHOLE repo, picked up by
# `scripts/tripwires.sh` regardless of what an item touched.
pytestmark = pytest.mark.tripwire

# This file lives at tests/authz/, so two parents up is the repo root.
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_SRC_ROOT: Path = _REPO_ROOT / "src" / "stackowl"
_AUTHZ_ROOT: Path = _SRC_ROOT / "authz"

_SEVERITY_NAMES: frozenset[str] = frozenset({"READ", "WRITE", "CONSEQUENTIAL", "ALL_SEVERITIES"})


def _iter_scannable_files() -> list[Path]:
    """Every ``src/stackowl`` module outside ``authz/`` — the new home is exempt."""
    files: list[Path] = []
    for py in sorted(_SRC_ROOT.rglob("*.py")):
        if _AUTHZ_ROOT in py.parents:
            continue
        files.append(py)
    return files


def _module_level_severity_violations(tree: ast.Module) -> list[str]:
    """Module-scope assignments (not nested in a function/class) that shadow a
    severity constant's name.

    Restricted to ``ast.Module.body`` (not ``ast.walk``) so a `READ`-named local
    inside a function body — unrelated to this vocabulary — is never flagged.
    """
    hits: list[str] = []
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in _SEVERITY_NAMES:
                hits.append(target.id)
    return hits


def _may_method_violations(tree: ast.Module) -> list[str]:
    """Classes anywhere in the module that define their own ``def may(self, ...)``.

    Restricted to methods (``FunctionDef`` nested directly under a ``ClassDef``)
    so an unrelated free function named ``may`` — none exist today, confirmed by
    ``grep -rn "^def may\\|    def may" src/stackowl`` — would not be flagged.
    """
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for member in node.body:
            if isinstance(member, ast.FunctionDef) and member.name == "may":
                hits.append(f"{node.name}.may")
    return hits


def _scan(files: list[Path]) -> list[tuple[str, str]]:
    """Return (src-relative path, violation description) pairs across *files*."""
    violations: list[tuple[str, str]] = []
    for py in files:
        try:
            source = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        rel = py.relative_to(_SRC_ROOT).as_posix()
        for name in _module_level_severity_violations(tree):
            violations.append((rel, f"module-level severity constant {name!r}"))
        for method in _may_method_violations(tree):
            violations.append((rel, f"its own `.may()` check ({method})"))
    return violations


class TestReExportIdentity:
    """`control_plane.auth` re-exports; it does not redefine."""

    def test_all_severities_is_the_same_object(self) -> None:
        assert CP_ALL_SEVERITIES is AUTHZ_ALL_SEVERITIES

    def test_read_is_the_same_object(self) -> None:
        assert CP_READ is AUTHZ_READ

    def test_write_is_the_same_object(self) -> None:
        assert CP_WRITE is AUTHZ_WRITE

    def test_consequential_is_the_same_object(self) -> None:
        assert CP_CONSEQUENTIAL is AUTHZ_CONSEQUENTIAL

    def test_control_principal_is_the_same_class(self) -> None:
        assert CPControlPrincipal is AuthzControlPrincipal


class TestOneHome:
    """AST scan: no module outside ``authz/`` may define its own copy."""

    def test_no_module_outside_authz_defines_a_severity_constant_or_may_check(self) -> None:
        offending = _scan(_iter_scannable_files())
        assert not offending, (
            "READ/WRITE/CONSEQUENTIAL/ALL_SEVERITIES and any `.may()` "
            "authorization check must live in `src/stackowl/authz/` (AD-7) — a "
            "second definition elsewhere is exactly the drift this guard exists "
            "to catch:\n  " + "\n  ".join(f"{f} :: {v}" for f, v in sorted(offending))
        )

    def test_detector_flags_a_reintroduced_severity_constant(self) -> None:
        """Self-check (red/green proof): a deliberately reintroduced violation
        is actually caught, not merely absent from the real tree by accident.
        """
        source = 'from typing import Final\n\nWRITE: Final = "write"\n'
        tree = ast.parse(source)
        assert _module_level_severity_violations(tree) == ["WRITE"]

    def test_detector_flags_a_reintroduced_may_method(self) -> None:
        """Self-check (red/green proof) for the `.may()` shape."""
        source = (
            "class RoguePrincipal:\n"
            "    def may(self, severity: str) -> bool:\n"
            "        return True\n"
        )
        tree = ast.parse(source)
        assert _may_method_violations(tree) == ["RoguePrincipal.may"]

    def test_detector_does_not_flag_a_reexport(self) -> None:
        """Self-check: `from stackowl.authz import READ as READ` is not a
        definition — `control_plane/auth.py`'s own shape must stay green.
        """
        source = "from stackowl.authz import READ as READ\n"
        tree = ast.parse(source)
        assert _module_level_severity_violations(tree) == []

    def test_detector_does_not_flag_an_unrelated_local_or_free_function(self) -> None:
        """Self-check: a `READ`-named local or an unrelated free `may()` is fine."""
        source = (
            "def handler() -> str:\n"
            "    READ = 'not the severity constant'\n"
            "    return READ\n"
            "\n"
            "def may(x: int) -> int:\n"
            "    return x\n"
        )
        tree = ast.parse(source)
        assert _module_level_severity_violations(tree) == []
        assert _may_method_violations(tree) == []
