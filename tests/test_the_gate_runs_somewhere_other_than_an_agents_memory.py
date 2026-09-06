"""The guards run when someone remembers. That is not a gate.

WHY THIS EXISTS. This programme is good at turning a prose rule into an executable
one — `progress_lint.py`, `tripwires.sh`, `validate_check.py`, `escalation_check.py`,
`map_check.py`, `claim_check.py`, and 56 files of `@pytest.mark.tripwire` guards. Every
one of them fires only when an agent voluntarily types the command.

MEASURED 2026-09-06:

  * `.git/hooks/` contains no installed hook, only the shipped samples.
  * `.pre-commit-config.yaml` exists and is not installed locally.
  * `.github/workflows/ci-v2.yml` runs pre-commit (ruff, mypy strict, file length) and
    the B1-B6 boundary scripts. It runs **no pytest at all**, so none of the 56
    tripwire files execute, and it never runs `progress_lint.py`.
  * Its `pull_request` trigger names `release/v2` only, so **a pull request to `main`
    triggers nothing.**

`tripwires.sh` opens by calling itself "the gate that must pass before ANY commit,
whatever the change touched", and argues that "a rule that says 'remember to run the
tripwires' is the same rule that already failed twice. This is the executable version."
It is still the remembering rule, because nothing schedules it.

That is CLAUDE.md failure mode #5 — built but not wired — applied to the enforcement
layer, which is the one place it is invisible: **a check that never runs also never
fails**, so its silence reads exactly like success.

This guard asserts the wiring, and it deliberately asserts it about the file that ships
to every clone rather than about a local hook. A hook installed on this box would be a
fix to this setup; the workflow is a fix to the platform.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOWS = _ROOT / ".github" / "workflows"
_CI = _WORKFLOWS / "ci-v2.yml"


def _ci() -> dict:
    return yaml.safe_load(_CI.read_text(encoding="utf-8"))


def _all_run_steps() -> str:
    """Every `run:` body in the workflow, concatenated."""
    doc = _ci()
    out: list[str] = []
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                out.append(step["run"])
    return "\n".join(out)


class TestTheCrossCuttingGuardsRunInCI:
    @pytest.mark.tripwire
    def test_ci_runs_the_gate(self) -> None:
        """THE WIRING. Not "CI runs some checks" — CI must run THE gate, the same
        script an agent runs, so the two can never diverge into different lists of
        what the gate contains."""
        assert "scripts/tripwires.sh" in _all_run_steps(), (
            "no CI step runs scripts/tripwires.sh, so the 56 tripwire files and "
            "progress_lint execute only when an agent remembers to type it"
        )

    @pytest.mark.tripwire
    def test_a_pull_request_to_main_triggers_ci(self) -> None:
        """`main` is the branch this programme commits to. A PR against it that
        triggers no workflow is a review with no verification behind it."""
        # PyYAML parses the bare key `on:` as the boolean True.
        doc = _ci()
        triggers = doc.get("on") or doc.get(True) or {}
        pr = (triggers.get("pull_request") or {}).get("branches") or []

        assert "main" in pr, (
            f"a pull request to main triggers nothing; ci-v2 watches {pr!r}"
        )


class TestNoWorkflowIsLeftFromARETIREDTREE:
    @pytest.mark.tripwire
    def test_no_workflow_builds_a_project_that_does_not_exist(self) -> None:
        """RETIRED MEANS DELETED, applied to CI.

        The v2->root migration moved this project to Python at the repo root. A
        workflow still running `npm ci` has no root `package.json` to install from,
        so it cannot succeed on any push — and a job that is always red is how a
        REAL red job stops being read.
        """
        offenders: list[str] = []
        for path in sorted(_WORKFLOWS.glob("*.y*ml")):
            body = path.read_text(encoding="utf-8")
            needs_node = "npm ci" in body or "npm run build" in body or "npm test" in body
            if needs_node and not (_ROOT / "package.json").exists():
                offenders.append(path.name)

        assert not offenders, (
            "these workflows build a Node project that this repository does not "
            f"contain, so they can never pass: {offenders}"
        )

    def test_the_guard_sees_a_real_population(self) -> None:
        """VACUITY CONTROL — an empty workflows directory would pass everything."""
        assert len(list(_WORKFLOWS.glob("*.y*ml"))) >= 2
        assert _CI.exists()
        assert _all_run_steps().strip(), "no run steps parsed out of ci-v2.yml"
