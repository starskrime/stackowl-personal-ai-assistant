"""A fact the trace copies at bind time must not be silently changed by a step.

WHY THIS EXISTS. DEBT-164 shipped the fix and this is the mandated other half — what
else does that same cause reach.

`bind_turn_context` copies FOURTEEN fields off `PipelineState` into `TraceContext`
before any step runs. Tools and the cost tracker read the trace, not the state. So any
field a step can change is a fact with two copies where only one moves, and the one that
moves is not the one anybody reads. That is exactly how 162 of 363 routed turns — 45% —
billed the wrong owl for three weeks.

MEASURED 2026-09-07, and the answer is narrow: of the fourteen, exactly ONE is changed by
a step — `owl_name`, in `triage.py` AND `dispatch.py`. Both are covered, because the fix
was placed in the step loop rather than in triage; `dispatch.py` was a site I did not know
about when I chose that location.

`creation_ceiling` LOOKS like a second instance and is not. `execute.py` builds
`state.evolve(creation_ceiling=None, task_envelope=None)` as a throwaway argument to
`compute_effective_bounds()`, purely to work out whether a denial came from the owl or
from the task. It is never the state that flows on. A naive scan reports it; so does a
scan that only asks "is this inside an assignment", because it sits inside
`owl_only = check_effective_bounds(...)`. Only "is the evolve the DIRECT value of the
return or assignment" separates them — which is why this guard asks that and not the
easier question. Getting this wrong in the crying-wolf direction is the failure this
programme refuses to ship.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_STEPS = _ROOT / "src/stackowl/pipeline/steps"
_SHARED = _ROOT / "src/stackowl/pipeline/backends/shared.py"

#: Fields the step loop already keeps in step with the state.
_SYNCED = {"owl_name"}


def _bound_from_state() -> set[str]:
    """The fields `bind_turn_context` copies out of the state, read from the source."""
    text = _SHARED.read_text(encoding="utf-8")
    block = re.search(r"trace_token = TraceContext\.start\((.*?)\n    \)", text, re.S)
    assert block, "could not find the TraceContext.start(...) call in shared.py"
    body = block.group(1)
    return set(re.findall(r"\w+=state\.(\w+)", body)) | set(
        re.findall(r"^\s+state\.(\w+),", body, re.M)
    )


def _step_mutated() -> dict[str, set[str]]:
    """Fields a step changes on the state THAT FLOWS ON.

    The evolve must BE the returned or assigned value. Nested deeper means it was
    constructed to be handed to something and discarded.
    """
    out: dict[str, set[str]] = {}
    for path in sorted(_STEPS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def take(node: ast.AST, _p: Path = path) -> None:
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "evolve"
            ):
                for kw in node.keywords:
                    if kw.arg:
                        out.setdefault(kw.arg, set()).add(_p.name)

        for node in ast.walk(tree):
            value = getattr(node, "value", None)
            if isinstance(node, (ast.Return, ast.Assign, ast.AnnAssign)) and value is not None:
                take(value)
    return out


class TestEveryStepMutableTraceFieldIsSynced:
    @pytest.mark.tripwire
    def test_no_bound_field_drifts_from_the_state(self) -> None:
        """THE CLASS, closed. A new field that is both copied into the trace and
        changed by a step reopens DEBT-164 under a different name."""
        mutated = _step_mutated()
        unsynced = {
            field: sorted(files)
            for field, files in mutated.items()
            if field in _bound_from_state() and field not in _SYNCED
        }

        assert not unsynced, (
            "these facts are copied into TraceContext before the steps run and then "
            "CHANGED by a step, so tools and cost rows read a stale value — the DEBT-164 "
            f"defect under a new name. Sync it in the step loop: {unsynced}"
        )

    def test_the_scan_sees_the_real_population(self) -> None:
        """VACUITY CONTROL — three ways this could pass by measuring nothing."""
        bound = _bound_from_state()
        mutated = _step_mutated()

        assert len(bound) >= 12, f"only parsed {len(bound)} bound fields: {sorted(bound)}"
        assert "owl_name" in bound, "the field the whole item is about is not parsed"
        assert len(mutated) >= 20, f"only found {len(mutated)} step-mutated fields"

    def test_the_owl_name_exemption_is_still_earned(self) -> None:
        """`_SYNCED` is an allowlist, and an allowlist entry nothing uses is dead
        weight — 'retired means deleted' applies to it too.

        ASKS WHETHER IT IS STILL MUTATED AT ALL, not which files do it. The first
        version pinned the exact set {triage.py, dispatch.py}, which would have failed
        the suite the day a THIRD step legitimately re-routed — a guard breaking on
        correct work, which is the failure this whole item is about. The set is
        allowed to grow: growth is already covered, because the sync sits in the step
        loop rather than in any one step.
        """
        mutated = _step_mutated()

        assert mutated.get("owl_name"), (
            "owl_name is no longer changed by any step, so the _SYNCED exemption "
            "guards nothing and should be deleted along with the sync it excuses"
        )


class TestTheScanDoesNotCryWolf:
    """The precision that makes the guard shippable, pinned as behaviour."""

    def test_a_throwaway_probe_is_not_counted(self) -> None:
        """`execute.py`'s deny-provenance probe evolves `creation_ceiling` into a state
        it hands to a function and discards. Counting it would fail a correct tree."""
        assert "creation_ceiling" not in _step_mutated(), (
            "the scan counted a throwaway evolve as a real mutation; a guard that fails "
            "on correct code is the failure this programme keeps paying for"
        )

    def test_it_still_counts_a_returned_evolve(self) -> None:
        """The control in the other direction — the scan must not be blind."""
        tree = ast.parse("def f(s):\n    return s.evolve(owl_name='x')\n")
        found = set()
        for node in ast.walk(tree):
            v = getattr(node, "value", None)
            if isinstance(node, ast.Return) and v is not None:
                if isinstance(v, ast.Call) and getattr(v.func, "attr", "") == "evolve":
                    found |= {kw.arg for kw in v.keywords if kw.arg}

        assert found == {"owl_name"}
