"""The gate that must pass before any commit — and its own guard.

WHY IT EXISTS. The item loop's `test` stage says "targeted paths with timeouts",
and paths get chosen by what the change looks related to. A CROSS-CUTTING guard —
one protecting a property of the whole repo — never looks related to anything, so
it is never selected. Two real defects shipped that way, both found later by
accident:

* `c80b5f85` — `usage_report.py` read the owner-governed `task_outcomes` with no
  `owner_id` predicate. A cross-tenant read. That item ran `tests/tools/meta` and
  `tests/startup`; the tripwire lives in `tests/tenancy`, so it never ran.
* `95a841c3` — deleting six modules left three of their entries in the
  owner-scope allowlist, so the allowlist claimed gaps that no longer existed.

A rule saying "remember to run the tripwires" is the same rule that already
failed twice, so it is now a script. This file guards the gate itself: the marker
must stay registered, the script must stay executable, and the loop's own
instructions must keep pointing at it. Without these, the gate rots quietly and
the next bypass ships exactly as the last two did.
"""

from __future__ import annotations

import os
import pathlib
import tomllib

import pytest

# This file is itself a cross-cutting guard.
pytestmark = pytest.mark.tripwire

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_the_marker_is_registered() -> None:
    """An unregistered marker makes `-m tripwire` a silent no-op under strict
    markers, and a gate that selects nothing passes everything."""
    cfg = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    markers = cfg["tool"]["pytest"]["ini_options"]["markers"]
    assert any(m.startswith("tripwire:") for m in markers)


def test_the_gate_script_exists_and_is_executable() -> None:
    script = _ROOT / "scripts" / "tripwires.sh"
    assert script.is_file(), "the gate is gone"
    assert os.access(script, os.X_OK), "the gate is not executable — it will not be run"


def test_the_gate_runs_the_marker_and_both_baselines() -> None:
    """Structural. Dropping any of these silently narrows the gate, which is the
    failure it exists to prevent, one level up."""
    body = (_ROOT / "scripts" / "tripwires.sh").read_text()
    assert "-m tripwire" in body
    assert "progress_lint" in body
    assert "ruff" in body
    assert "mypy" in body


def test_the_known_guards_carry_the_marker() -> None:
    """The two that caught the shipped defects. If either loses the marker it
    stops being run by the gate and we are back where we started."""
    for rel in ("tests/tenancy/test_no_owner_scope_bypass.py",
                "tests/test_double_conformance.py"):
        body = (_ROOT / rel).read_text()
        assert "pytest.mark.tripwire" in body, f"{rel} is no longer in the gate"


def test_the_loop_instructions_point_at_the_gate() -> None:
    """A gate nobody is told to run is decoration. Both the skill that drives the
    loop and the file every session reads must name it."""
    skill = (_ROOT / ".claude" / "skills" / "item-loop" / "SKILL.md").read_text()
    claude = (_ROOT / "CLAUDE.md").read_text()
    assert "tripwires.sh" in skill, "the item-loop skill no longer requires the gate"
    assert "tripwires.sh" in claude, "CLAUDE.md no longer names the gate"
