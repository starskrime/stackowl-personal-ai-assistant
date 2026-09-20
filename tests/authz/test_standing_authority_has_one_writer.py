"""Story 4.6 AC2 -- ``standing_authority``'s writers (``authz.standing_
authority.grant``/``.revoke``) and ``authz.requester``'s internals are
reachable only from ``authz/``, ``commands/spec/`` and a subsystem's own
``*/commands.py`` handler module.

Modelled on ``tests/authz/test_severities_and_principal_have_one_home.py``'s
own AST-scan style (module-level ``pytestmark = pytest.mark.tripwire`` so
``scripts/tripwires.sh``/``pytest -m tripwire`` picks this up regardless of
what a change touched).

WHY THIS MATTERS (FR37): ``grant``/``revoke`` are declared
``severity="consequential"`` (``authz/commands.py``), so the action-policy
gate already forces step-up for every requester kind, structurally — but
that guarantee only holds if NOTHING can call the write functions directly,
bypassing the command declaration entirely. This tripwire is the other half
of that argument: it fails the build the moment a tool, an owl-facing
surface, or any file outside the allowed set imports either write function,
or reaches into ``authz.requester``'s private internals (the same "who is
asking" plumbing a bypass would need to forge).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.tripwire

# This file lives at tests/authz/, so two parents up is the repo root.
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_SRC_ROOT: Path = _REPO_ROOT / "src" / "stackowl"

#: The two functions that are the ONLY way `standing_authority` is ever
#: written (`find_active` is read-only and explicitly not restricted).
_WRITE_FUNCTIONS = frozenset({"grant", "revoke"})


def _is_allowed_file(py: Path) -> bool:
    """``authz/`` (any depth), ``commands/spec/`` (any depth), or a
    subsystem's own ``*/commands.py`` handler module (e.g.
    ``scheduler/commands.py``, ``authz/commands.py`` itself)."""
    rel = py.relative_to(_SRC_ROOT).as_posix()
    if rel.startswith("authz/"):
        return True
    if rel.startswith("commands/spec/"):
        return True
    return py.name == "commands.py"


def _requester_internal_names(tree: ast.Module) -> frozenset[str]:
    """Every module-level name defined in ``authz/requester.py`` that is NOT
    part of its public surface (``authz/__init__.py``'s own re-export list:
    ``RequesterKind``, ``principal_for``, ``requester_kind_from_trace``)."""
    public = {"RequesterKind", "principal_for", "requester_kind_from_trace"}
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return frozenset(names - public)


def _requester_internal_names_live() -> frozenset[str]:
    requester_py = _SRC_ROOT / "authz" / "requester.py"
    tree = ast.parse(requester_py.read_text(encoding="utf-8"))
    return _requester_internal_names(tree)


def _violations_in(py: Path, internal_names: frozenset[str]) -> list[str]:
    """Named ``from stackowl.authz.standing_authority import grant/revoke``
    (or ``from stackowl.authz.requester import <internal>``) -- the direct
    named-import form."""
    tree = ast.parse(py.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if module == "stackowl.authz.standing_authority":
            for alias in node.names:
                if alias.name in _WRITE_FUNCTIONS:
                    hits.append(f"standing_authority.{alias.name}")
        elif module == "stackowl.authz.requester":
            for alias in node.names:
                if alias.name in internal_names:
                    hits.append(f"requester.{alias.name}")
    return hits


def _dotted_chain(node: ast.expr) -> str | None:
    """Reconstruct ``a.b.c`` from a plain ``Name``/``Attribute`` chain (e.g.
    the ``standing_authority`` in ``standing_authority.grant(...)``, or the
    ``stackowl.authz.standing_authority`` in ``stackowl.authz.
    standing_authority.grant(...)``). ``None`` for anything else (a call
    result, a subscript, …) -- those can never be a bound module reference."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        return None
    return ".".join(reversed(parts))


def _submodule_aliases(tree: ast.Module) -> frozenset[str]:
    """Every local name THIS file binds directly to the
    ``stackowl.authz.standing_authority`` submodule object itself (never one
    of its functions) -- via ``import stackowl.authz.standing_authority as
    X`` or ``from stackowl.authz import standing_authority [as X]``. A bare
    ``import stackowl.authz.standing_authority`` (no alias) binds only the
    top-level name ``stackowl`` and is caught separately, by the literal
    ``stackowl.authz.standing_authority.<attr>`` dotted-chain check below."""
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "stackowl.authz.standing_authority" and alias.asname:
                    aliases.add(alias.asname)
        elif isinstance(node, ast.ImportFrom) and node.module == "stackowl.authz":
            for alias in node.names:
                if alias.name == "standing_authority":
                    aliases.add(alias.asname or "standing_authority")
    return frozenset(aliases)


def _attribute_violations_in(py: Path) -> list[str]:
    """``<X>.grant``/``<X>.revoke`` attribute access, where ``<X>`` resolves
    (via :func:`_dotted_chain`) to either a local alias bound to the
    ``standing_authority`` submodule (:func:`_submodule_aliases`) or the
    literal ``stackowl.authz.standing_authority`` dotted chain -- the
    ``import stackowl.authz.standing_authority`` (no alias) plus
    ``stackowl.authz.standing_authority.grant(...)`` bypass form."""
    tree = ast.parse(py.read_text(encoding="utf-8"))
    aliases = _submodule_aliases(tree)
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr not in _WRITE_FUNCTIONS:
            continue
        chain = _dotted_chain(node.value)
        if chain is None:
            continue
        if chain in aliases or chain == "stackowl.authz.standing_authority":
            hits.append(f"standing_authority.{node.attr}")
    return hits


def _scan() -> list[tuple[str, str]]:
    internal_names = _requester_internal_names_live()
    violations: list[tuple[str, str]] = []
    for py in sorted(_SRC_ROOT.rglob("*.py")):
        if _is_allowed_file(py):
            continue
        rel = py.relative_to(_SRC_ROOT).as_posix()
        for hit in _violations_in(py, internal_names):
            violations.append((rel, hit))
        for hit in _attribute_violations_in(py):
            violations.append((rel, hit))
    return violations


def test_no_file_outside_the_allowlist_reaches_standing_authoritys_writers_or_requester_internals() -> None:
    offending = _scan()
    assert not offending, (
        "standing_authority.grant/.revoke and authz.requester's internals "
        "may be imported only from authz/, commands/spec/ and a subsystem's "
        "own */commands.py handler module (FR37) -- found:\n  "
        + "\n  ".join(f"{f} imports {v!r}" for f, v in sorted(offending))
    )


def test_requester_internal_names_are_computed_correctly() -> None:
    """Self-check: `_CREDENTIAL_ID` (the one private module-level constant in
    `authz/requester.py` today) is classified internal; the public API is not."""
    internal_names = _requester_internal_names_live()
    assert "_CREDENTIAL_ID" in internal_names
    assert "RequesterKind" not in internal_names
    assert "principal_for" not in internal_names
    assert "requester_kind_from_trace" not in internal_names


def test_the_detector_actually_catches_a_reintroduced_write_function_import() -> None:
    """Self-check (red/green proof): a reintroduced import of `grant` from
    outside the allowlist is actually flagged, not merely absent by accident."""
    source = "from stackowl.authz.standing_authority import grant\n"
    tree = ast.parse(source)
    hits = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "stackowl.authz.standing_authority"
        for alias in node.names
        if alias.name in _WRITE_FUNCTIONS
    ]
    assert hits == ["grant"]


def test_the_detector_does_not_flag_find_active() -> None:
    """Self-check: `find_active` (read-only) is never flagged."""
    source = "from stackowl.authz.standing_authority import find_active\n"
    tree = ast.parse(source)
    hits = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "stackowl.authz.standing_authority"
        for alias in node.names
        if alias.name in _WRITE_FUNCTIONS
    ]
    assert hits == []


def test_the_detector_catches_the_from_import_submodule_bypass() -> None:
    """Self-check (red/green proof): ``from stackowl.authz import
    standing_authority`` followed by ``standing_authority.grant(...)`` --
    the exact idiom ``authz/commands.py`` itself uses -- is caught when it
    appears OUTSIDE the allowlist (this synthetic source is scanned
    directly, not written to a file under the allowlist)."""
    source = (
        "from stackowl.authz import standing_authority\n"
        "\n"
        "async def bypass():\n"
        "    return await standing_authority.grant(db, scope_kind='job')\n"
    )
    tree = ast.parse(source)
    aliases = _submodule_aliases(tree)
    hits = [
        f"standing_authority.{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in _WRITE_FUNCTIONS
        and _dotted_chain(node.value) in aliases
    ]
    assert hits == ["standing_authority.grant"]


def test_the_detector_catches_the_dotted_import_bypass() -> None:
    """Self-check (red/green proof): a bare ``import stackowl.authz.
    standing_authority`` (no alias) followed by the fully-qualified
    ``stackowl.authz.standing_authority.revoke(...)`` call is also caught."""
    source = (
        "import stackowl.authz.standing_authority\n"
        "\n"
        "async def bypass():\n"
        "    return await stackowl.authz.standing_authority.revoke(db, scope_kind='job')\n"
    )
    tree = ast.parse(source)
    hits = [
        f"standing_authority.{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in _WRITE_FUNCTIONS
        and _dotted_chain(node.value) == "stackowl.authz.standing_authority"
    ]
    assert hits == ["standing_authority.revoke"]


def test_attribute_violations_in_catches_both_bypass_forms_on_a_real_file(
    tmp_path: Path,
) -> None:
    """End-to-end (not just the synthetic-AST self-checks above):
    ``_attribute_violations_in`` -- the function ``_scan()`` actually calls
    -- run against a REAL file on disk catches both bypass forms."""
    py = tmp_path / "bypass.py"
    py.write_text(
        "from stackowl.authz import standing_authority\n"
        "import stackowl.authz.standing_authority as sa\n"
        "\n"
        "async def bypass_one():\n"
        "    return await standing_authority.grant(db, scope_kind='job')\n"
        "\n"
        "async def bypass_two():\n"
        "    return await sa.revoke(db, scope_kind='job')\n",
        encoding="utf-8",
    )
    hits = _attribute_violations_in(py)
    assert sorted(hits) == ["standing_authority.grant", "standing_authority.revoke"]


def test_the_detector_actually_catches_a_reintroduced_requester_internal_import() -> None:
    """Self-check (red/green proof) for the requester-internals half."""
    source = "from stackowl.authz.requester import _CREDENTIAL_ID\n"
    tree = ast.parse(source)
    internal_names = frozenset({"_CREDENTIAL_ID"})
    hits = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "stackowl.authz.requester"
        for alias in node.names
        if alias.name in internal_names
    ]
    assert hits == ["_CREDENTIAL_ID"]


def test_the_scan_has_a_real_denominator() -> None:
    total = sum(1 for _ in _SRC_ROOT.rglob("*.py"))
    assert total > 0, "src/stackowl scan found no files -- the guard is vacuous"
