"""Story 4.3/4.7 — the four AD-1 "one door" tripwires.

1. Import-boundary (AD-7): ``commands/spec/`` imports only ``stackowl.authz``,
   ``stackowl.pipeline.durable``, itself, ``stackowl.infra`` (cross-cutting
   logging — exempted the same way ``pydantic`` is; never a subsystem a later
   story could delete out from under this package), stdlib and pydantic.
2. Every ``JobScheduler`` mutator (Story 4.3's ``pause``/``resume``; Story
   4.7's ``snooze``/``stop_job``/``create_job``/``update_job``/``run_now``)
   is called only from ``scheduler/commands.py``'s handlers (plus each
   group's own named, pre-existing, still-unmigrated exception) — proving
   ``cronjob.py``'s and ``owl_schedule.py``'s migrations actually happened
   and no new direct caller appeared.
3. No subsystem mutator (``JobScheduler``) is instantiated under
   ``src/stackowl/gateway/``.
4. No ``eval``/``exec`` call and no ``getattr``-based command-type-keyed
   dynamic dispatch anywhere in ``commands/spec/`` — the handler registry is
   a closed, explicit dict (AD-1's Boundaries).
5. ``ObjectiveStore.create``/``.add_subgoals`` are called only from
   ``objectives/commands.py`` — the SAME AD-1 guarantee as item 2, proven for
   the ``objectives`` domain so the story's advertised coverage is symmetric
   between scheduling and objectives, not scheduling-only.

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
# 2. Every mutator is called only from the handler registry
# =============================================================================

#: One group per call-shape, each mapped to (the method name(s) checked
#: together, the files legitimately calling ANY of them directly today).
#: `scheduler/commands.py` — the ONE handler-registry caller — belongs to
#: EVERY group (Story 4.3's pilot pair; Story 4.7's remaining five). The
#: other names are each group's own PRE-EXISTING, still-unmigrated
#: exception, explicitly out of scope: `tools/meta/owl_build.py` (owl
#: create/dna management, owned by 4.9 per the 4.2 census) still calls
#: `.pause`/`.resume` directly; `webhooks/receiver.py`'s `create_job` call
#: is explicitly named OUT of this story's scope (spec-4-7 Boundaries: "Do
#: not migrate webhooks/receiver.py's direct create_job call"). Neither
#: `tools/scheduling/cronjob.py` NOR `tools/scheduling/owl_schedule.py`
#: appears in ANY group — both are now FULLY migrated (Story 4.3's pilot
#: pair plus this story's remaining five mutators).
_MUTATOR_GROUPS: dict[str, tuple[tuple[str, ...], frozenset[str]]] = {
    "pause/resume": (
        ("pause", "resume"),
        frozenset({"scheduler/commands.py", "tools/meta/owl_build.py"}),
    ),
    "snooze": (
        ("snooze",),
        frozenset({"scheduler/commands.py"}),
    ),
    "stop_job": (
        ("stop_job",),
        frozenset({"scheduler/commands.py"}),
    ),
    "create_job": (
        ("create_job",),
        frozenset({"scheduler/commands.py", "webhooks/receiver.py"}),
    ),
    "update_job": (
        ("update_job",),
        frozenset({"scheduler/commands.py"}),
    ),
    "run_now": (
        ("run_now",),
        frozenset({"scheduler/commands.py"}),
    ),
}


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


def _calls_any(py: Path, method_names: tuple[str, ...]) -> bool:
    """True iff *py* contains a ``<expr>.<name>(...)`` CALL for any name in
    *method_names* (never a ``def <name>`` — the definition site itself)."""
    tree = ast.parse(py.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in method_names:
                return True
    return False


@pytest.mark.parametrize("group_name", sorted(_MUTATOR_GROUPS))
def test_job_scheduler_mutator_is_called_only_from_the_allowlist(group_name: str) -> None:
    method_names, allowlist = _MUTATOR_GROUPS[group_name]
    label = "/".join(method_names)
    actual: set[str] = set()
    for py in _files_importing_job_scheduler():
        rel = py.relative_to(_SRC_ROOT).as_posix()
        if rel == "scheduler/scheduler.py":
            continue  # the definition site — never calls itself
        if _calls_any(py, method_names):
            actual.add(rel)

    unexpected = actual - allowlist
    assert not unexpected, (
        f"JobScheduler.{label} called from outside the allowlist — every "
        "state-changing call must go through scheduler/commands.py's handler "
        f"registry (AD-1): {sorted(unexpected)}"
    )
    assert "tools/scheduling/cronjob.py" not in actual, (
        f"cronjob.py still calls JobScheduler.{label} directly — its "
        "migration (Story 4.3/4.7) has regressed"
    )
    assert "tools/scheduling/owl_schedule.py" not in actual, (
        f"owl_schedule.py still calls JobScheduler.{label} directly — its "
        "migration (Story 4.7) has regressed"
    )
    stale = allowlist - actual
    assert not stale, (
        f"the allowlist names caller(s) that no longer call {label}: "
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


def test_calls_any_detects_every_declared_group(tmp_path: Path) -> None:
    """Self-check (red/green proof), for the widened detector: each group's
    OWN method name(s) are actually caught by :func:`_calls_any`, not merely
    absent from the real tree by accident."""
    for method_names in (m for m, _ in _MUTATOR_GROUPS.values()):
        source = f"async def f(scheduler):\n    await scheduler.{method_names[0]}(job_id)\n"
        py = tmp_path / f"probe_{method_names[0]}.py"
        py.write_text(source, encoding="utf-8")
        assert _calls_any(py, method_names) is True
        assert _calls_any(py, ("__never_matches__",)) is False


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


# =============================================================================
# 5. ObjectiveStore.create/.add_subgoals is called only from objectives/commands.py
# =============================================================================

#: The ONE handler-registry caller (Story 4.7's own `objectives/commands.py`,
#: mirrors `scheduler/commands.py`'s placement exactly) — no other file
#: today legitimately creates an objective row or appends sub-goals to it.
_OBJECTIVE_STORE_METHODS: tuple[str, ...] = ("create", "add_subgoals")
_ALLOWED_OBJECTIVE_STORE_CALLERS = frozenset({"objectives/commands.py"})


def _files_importing_objective_store() -> list[Path]:
    files: list[Path] = []
    for py in sorted(_SRC_ROOT.rglob("*.py")):
        try:
            source = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover — not our concern
            continue
        if "ObjectiveStore" in source:
            files.append(py)
    return files


def test_objective_store_create_and_add_subgoals_are_called_only_from_the_allowlist() -> None:
    actual: set[str] = set()
    for py in _files_importing_objective_store():
        rel = py.relative_to(_SRC_ROOT).as_posix()
        if rel == "objectives/store.py":
            continue  # the definition site — never calls itself
        if _calls_any(py, _OBJECTIVE_STORE_METHODS):
            actual.add(rel)

    unexpected = actual - _ALLOWED_OBJECTIVE_STORE_CALLERS
    assert not unexpected, (
        "ObjectiveStore.create/.add_subgoals called from outside the "
        "allowlist — every state-changing call must go through "
        f"objectives/commands.py's handler (AD-1): {sorted(unexpected)}"
    )
    assert "tools/scheduling/objective_tool.py" not in actual, (
        "objective_tool.py still calls ObjectiveStore.create/.add_subgoals "
        "directly — its migration (Story 4.7) has regressed"
    )
    stale = _ALLOWED_OBJECTIVE_STORE_CALLERS - actual
    assert not stale, (
        f"the allowlist names caller(s) that no longer call create/"
        f"add_subgoals: {sorted(stale)} — a subset check would not catch "
        "this drift"
    )
