"""A complete, tested pruner and a complete, documented setting, joined to nothing.

WHY THIS EXISTS. MEASURED 2026-09-08, from the live database: `audit_log` held 11,353
rows spanning ~105 days against an advertised 90-day bound. Neither half of the feature
was missing:

  * `audit/retention.py` has a careful `AuditRetention.prune()` — it lifts the no-delete
    trigger, deletes past the cutoff, restores the trigger inside one EXCLUSIVE
    transaction with rollback, holds DELETION records to their own longer horizon, and
    appends a prune record. It is covered by `tests/test_story_12_4.py`.
  * `GovernanceSettings.audit_retention_days` was declared, defaulted, described and
    range-checked.

`AuditRetention` was constructed in exactly ONE place in the repository — that test — and
NOWHERE in `src/`. The setting had exactly ONE reference in `src/`: its own declaration.
So the pruner never ran, the number meant nothing, and every instrument looked healthy,
because both halves individually pass review.

THAT IS THE VERSION OF "BUILT BUT NOT WIRED" THAT IS HARDEST TO SEE. The usual shape is
a capability with an obvious hole. Here there was no hole — there was a missing EDGE, and
an edge is invisible to any check that reads one file at a time.

WHAT IS ASSERTED is the edge, not the two nodes: a component whose only purpose is to run
on a schedule must be constructed somewhere that is not a test, and the setting that
parameterises it must be read. A future refactor that moves the wiring is fine; one that
drops it puts the platform back to keeping audit rows forever while advertising that it
does not.

Bakir set the horizon on 2026-09-08: "Audit can be deleted after 14 days. Fix code."
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"

#: Components whose entire purpose is to be run by something else. A construction site
#: in `src/` is the edge that makes them real.
_MUST_BE_CONSTRUCTED_IN_SRC = {"AuditRetention"}

#: Settings that must be READ, not merely declared. `audit_retention_days` was read by
#: nothing while the table it governs grew past its own bound.
_MUST_BE_READ = {"audit_retention_days"}


def _src_files() -> list[Path]:
    return sorted(_SRC.rglob("*.py"))


def _construction_sites(name: str) -> list[str]:
    out: list[str] = []
    for path in _src_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if called == name:
                out.append(f"{path.relative_to(_ROOT)}:{node.lineno}")
    return out


@pytest.mark.tripwire
@pytest.mark.parametrize("component", sorted(_MUST_BE_CONSTRUCTED_IN_SRC))
def test_a_scheduled_component_is_constructed_somewhere_in_src(component: str) -> None:
    sites = _construction_sites(component)
    assert sites, (
        f"{component} is defined and tested but constructed NOWHERE in src/ — it can "
        "never run in production. That is the defect this guard exists for: both halves "
        "complete, the edge between them missing."
    )


@pytest.mark.tripwire
@pytest.mark.parametrize("setting", sorted(_MUST_BE_READ))
def test_a_declared_setting_is_read_by_something(setting: str) -> None:
    """A setting with one reference — its own declaration — is a number that means nothing.

    Counts references OUTSIDE `config/`, so the declaration and its own schema plumbing
    do not vouch for themselves.
    """
    readers = [
        f"{p.relative_to(_ROOT)}"
        for p in _src_files()
        if "config" not in p.parts and setting in p.read_text(encoding="utf-8")
    ]
    assert readers, (
        f"{setting} is declared and documented but read by nothing outside config/ — "
        "it advertises a bound the platform does not apply."
    )


@pytest.mark.tripwire
def test_the_decay_job_runs_the_audit_leg() -> None:
    """The specific edge, named — the decay pass must actually call the pruner.

    Asserted against the handler's source rather than by running a job, because the
    point is that the CALL exists at all; whether it deleted anything on a given day
    depends on the data.
    """
    path = _SRC / "stackowl" / "scheduler" / "handlers" / "knowledge_prune.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    # AST, NOT SUBSTRING — and the first version of this was substring, which a mutation
    # walked straight through: replacing the real `self._audit_retention.prune()` with
    # `pruned = 0` left the test green, because this file's own DOCSTRING contains the
    # text ".prune()". A guard satisfied by prose is the failure this repo has already
    # paid for elsewhere; the call has to be a call.
    calls = {
        getattr(n.func, "attr", None)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    names = {
        n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
    }
    assert "_run_audit_retention" in names, "the audit leg is gone from the decay pass"
    assert "prune" in calls, "the decay pass no longer CALLS the pruner"
