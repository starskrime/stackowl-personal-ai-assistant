"""Story 4.3 — the four AD-1 "one door" tripwires.

1. Import-boundary (AD-7): ``commands/spec/`` imports only ``stackowl.authz``,
   ``stackowl.pipeline.durable``, itself, ``stackowl.infra`` (cross-cutting
   logging — exempted the same way ``pydantic`` is; never a subsystem a later
   story could delete out from under this package), stdlib and pydantic.
2. The mutator (``JobScheduler.pause``/``.resume``) is called only from
   ``scheduler/commands.py``'s handlers — proving ``cronjob.py``'s migration
   actually happened and no new direct caller appeared.
3. No subsystem mutator (``JobScheduler``) is instantiated under
   ``src/stackowl/gateway/``.
4. No ``eval``/``exec`` call and no ``getattr``-based command-type-keyed
   dynamic dispatch anywhere in ``commands/spec/`` — the handler registry is
   a closed, explicit dict (AD-1's Boundaries).

Modelled on ``tests/authz/test_severities_and_principal_have_one_home.py``'s
own AST-scan style (module-level ``pytestmark = pytest.mark.tripwire`` so
``scripts/tripwires.sh``/``pytest -m tripwire`` picks these up regardless of
what a change touched).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.tripwire

_REPO_ROOT: Path = Path(__file__).resolve().parents[3]
_SRC_ROOT: Path = _REPO_ROOT / "src" / "stackowl"
_SPEC_ROOT: Path = _SRC_ROOT / "commands" / "spec"
_GATEWAY_ROOT: Path = _SRC_ROOT / "gateway"


# =============================================================================
# 1. Import-boundary (AD-7)
# =============================================================================

#: Every top-level import `commands/spec/*.py` may reach — checked as a
#: dotted-name PREFIX match (`"stackowl.authz.severity"` matches the
#: `"stackowl.authz"` prefix). Intra-package (`stackowl.commands.spec.*`)
#: imports are always allowed — a package may import its own siblings.
_ALLOWED_STACKOWL_PREFIXES = (
    "stackowl.authz",
    "stackowl.pipeline.durable",
    "stackowl.commands.spec",
    "stackowl.infra.observability",
)

#: A handful of modules this codebase treats as effectively stdlib for a
#: restricted package (mirrors what every file in `commands/spec/` actually
#: imports today) — never a subsystem.
_ALLOWED_THIRD_PARTY = ("pydantic",)


def _is_allowed(module: str) -> bool:
    if not module:
        return True  # a bare `from . import x` — always intra-package
    if module.startswith("stackowl."):
        return any(module.startswith(p) for p in _ALLOWED_STACKOWL_PREFIXES)
    if module == "stackowl":
        return False
    top = module.split(".")[0]
    if top in _ALLOWED_THIRD_PARTY:
        return True
    # stdlib: importable from a bare `python -c "import <top>"` with no
    # third-party package installed for it — checked via sys.stdlib_module_names
    # (3.10+), the authoritative list rather than a hand-maintained one.
    return top in sys.stdlib_module_names


def _type_checking_lines(tree: ast.Module) -> set[int]:
    """Line numbers inside an ``if TYPE_CHECKING:`` block — exempt, mirroring
    this codebase's own widespread use of that guard (e.g.
    ``pipeline/durable/store.py``'s own ``if TYPE_CHECKING:`` import of
    ``ProactiveJobDeliverer``) for a type-only reference with zero runtime
    footprint."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_type_checking = (
            (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
            or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")
        )
        if not is_type_checking:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.stmt):
                lines.add(sub.lineno)
    return lines


def _violations_in(py: Path) -> list[str]:
    tree = ast.parse(py.read_text(encoding="utf-8"))
    exempt = _type_checking_lines(tree)
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.lineno in exempt:
                continue
            module = node.module or ""
            if node.level and not module:
                continue  # a bare relative `from . import x`
            if not _is_allowed(module):
                hits.append(module)
        elif isinstance(node, ast.Import):
            if node.lineno in exempt:
                continue
            for alias in node.names:
                if not _is_allowed(alias.name):
                    hits.append(alias.name)
    return hits


def test_commands_spec_imports_only_authz_and_pipeline_durable() -> None:
    offending: list[tuple[str, str]] = []
    for py in sorted(_SPEC_ROOT.glob("*.py")):
        for module in _violations_in(py):
            offending.append((py.name, module))
    assert not offending, (
        "commands/spec/ may import only stackowl.authz, "
        "stackowl.pipeline.durable, itself, stdlib and pydantic (AD-7) — "
        "found:\n  " + "\n  ".join(f"{f} imports {m!r}" for f, m in offending)
    )


def test_the_import_boundary_detector_actually_catches_a_violation() -> None:
    """Self-check (red/green proof): a reintroduced cross-subsystem import is
    actually flagged, not merely absent from the real tree by accident."""
    source = "from stackowl.journal import record\n"
    tree = ast.parse(source)
    hits = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and not _is_allowed(node.module or "")
    ]
    assert hits == ["stackowl.journal"]


def test_the_import_boundary_detector_does_not_flag_type_checking_imports() -> None:
    """Self-check: a TYPE_CHECKING-guarded import (e.g. `submit.py`'s
    `DbPool` annotation) is exempt, matching this codebase's own convention."""
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from stackowl.db.pool import DbPool\n"
    )
    tree = ast.parse(source)
    exempt = _type_checking_lines(tree)
    violations = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.lineno not in exempt
        and not _is_allowed(node.module or "")
    ]
    assert violations == []


# =============================================================================
# 2. The mutator is called only from the handler registry
# =============================================================================

#: Files legitimately calling `JobScheduler.pause`/`.resume` directly today.
#: `scheduler/commands.py` is the ONE handler-registry caller this story
#: adds; `tools/scheduling/owl_schedule.py`/`tools/meta/owl_build.py` are
#: PRE-EXISTING, unmigrated callers explicitly out of this story's scope
#: (owned by 4.7/4.9 per the 4.2 census — see spec-4-3's Boundaries: "Only
#: cronjob pause/resume migrate this story"). `cronjob.py` is deliberately
#: ABSENT — its presence here would mean the pilot migration regressed.
_ALLOWED_PAUSE_RESUME_CALLERS = frozenset({
    "scheduler/commands.py",
    "tools/scheduling/owl_schedule.py",
    "tools/meta/owl_build.py",
})


def _files_importing_job_scheduler() -> list[Path]:
    files: list[Path] = []
    for py in sorted(_SRC_ROOT.rglob("*.py")):
        try:
            source = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover — not our concern
            continue
        if "JobScheduler" in source:
            files.append(py)
    return files


def _calls_pause_or_resume(py: Path) -> bool:
    """True iff *py* contains a ``<expr>.pause(...)``/``.resume(...)`` CALL
    (never a ``def pause``/``def resume`` — the definition site itself)."""
    tree = ast.parse(py.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in ("pause", "resume"):
                return True
    return False


def test_job_scheduler_pause_and_resume_are_called_only_from_the_allowlist() -> None:
    actual: set[str] = set()
    for py in _files_importing_job_scheduler():
        rel = py.relative_to(_SRC_ROOT).as_posix()
        if rel == "scheduler/scheduler.py":
            continue  # the definition site — never calls itself
        if _calls_pause_or_resume(py):
            actual.add(rel)

    unexpected = actual - _ALLOWED_PAUSE_RESUME_CALLERS
    assert not unexpected, (
        "JobScheduler.pause/.resume called from outside the allowlist — every "
        "state-changing call must go through scheduler/commands.py's handler "
        f"registry (AD-1): {sorted(unexpected)}"
    )
    assert "tools/scheduling/cronjob.py" not in actual, (
        "cronjob.py still calls JobScheduler.pause/.resume directly — the "
        "pilot migration (this story's central AC) has regressed"
    )
    stale = _ALLOWED_PAUSE_RESUME_CALLERS - actual
    assert not stale, (
        f"the allowlist names caller(s) that no longer call pause/resume: "
        f"{sorted(stale)} — a subset check would not catch this drift"
    )


def test_the_mutator_detector_actually_catches_a_direct_call() -> None:
    """Self-check: a reintroduced direct call is flagged, not merely absent."""
    source = "async def f(scheduler):\n    await scheduler.pause(job_id)\n"
    tree = ast.parse(source)
    assert any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "pause"
        for n in ast.walk(tree)
    )


# =============================================================================
# 3. No subsystem mutator instantiated under gateway/
# =============================================================================

def test_no_job_scheduler_is_instantiated_under_gateway() -> None:
    offending: list[str] = []
    for py in sorted(_GATEWAY_ROOT.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "JobScheduler"
            ):
                offending.append(py.relative_to(_SRC_ROOT).as_posix())
    assert not offending, (
        "a subsystem mutator (JobScheduler) is instantiated under gateway/ — "
        f"gateway must only forward frames, never drive a mutator directly: "
        f"{offending}"
    )


def test_the_gateway_scan_has_a_real_denominator() -> None:
    total = sum(1 for _ in _GATEWAY_ROOT.rglob("*.py"))
    assert total > 0, "gateway/ scan found no files — the guard is vacuous"


# =============================================================================
# 4. No eval/exec, and no getattr-based dynamic dispatch, in commands/spec/
# =============================================================================


def _dynamic_dispatch_violations(py: Path) -> list[str]:
    """``eval(``/``exec(`` calls (never legitimate here) and ``getattr(``
    calls (AD-1's "closed, explicit dict" rule forbids resolving a command
    type or handler via ``getattr`` — the two registries are plain dicts,
    looked up by ``[]``/``.get()``, never by attribute name)."""
    tree = ast.parse(py.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in ("eval", "exec", "getattr"):
            hits.append(func.id)
    return hits


def test_commands_spec_has_no_eval_exec_or_getattr_dispatch() -> None:
    offending: list[tuple[str, str]] = []
    for py in sorted(_SPEC_ROOT.glob("*.py")):
        for name in _dynamic_dispatch_violations(py):
            offending.append((py.name, name))
    assert not offending, (
        "commands/spec/ must never eval/exec or resolve a command type/handler "
        "via getattr — the handler registry is a closed, explicit dict "
        "(AD-1's Boundaries). Found:\n  "
        + "\n  ".join(f"{f} calls {n}(...)" for f, n in offending)
    )


def test_the_dynamic_dispatch_detector_actually_catches_a_violation() -> None:
    """Self-check (red/green proof): a reintroduced eval/exec/getattr call is
    actually flagged, not merely absent from the real tree by accident."""
    for shape, expected in (
        ("eval(user_input)", "eval"),
        ("exec(user_input)", "exec"),
        ("getattr(registry, command_type)", "getattr"),
    ):
        tree = ast.parse(shape)
        hits = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("eval", "exec", "getattr")
        ]
        assert hits == [expected]


def test_the_dynamic_dispatch_detector_does_not_flag_a_dict_lookup() -> None:
    """Self-check: the real, closed-dict shape this package actually uses
    (``self._handlers.get(command_type)``) is never flagged."""
    source = "def f(self, command_type):\n    return self._handlers.get(command_type)\n"
    tree = ast.parse(source)
    hits = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("eval", "exec", "getattr")
    ]
    assert hits == []
