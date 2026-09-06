"""A recurring goal the USER asked for replays a missed run. A sweep does not.

WHY THIS EXISTS. `Scheduler.recover` re-arms overdue jobs at boot and will DISPATCH a
missed one inside a 24h window — but for a recurring job only when `replay_missed` is
set:

    if (job.replay_missed or not self._is_recurring(job)) and inside_window:

MEASURED 2026-09-06: `replay_missed` is 0 on **all 144 rows**, `replay_missed=True` is
passed in exactly ONE place in the whole tree — a test — and the flag appears on no
tool, command or config surface. So the condition reduces to `not
self._is_recurring(job)`: the flag contributes nothing and a recurring job that misses
its slot never runs that occurrence. Reader, column, and persistence plumbing all
built; no writer anywhere. CLAUDE.md names the shape and its corollary — "a feature
ships ON: if nothing sets the flag, you shipped decoration".

It is not hypothetical. On 2026-09-05 `goal_execution-88f54aa1` (daily@17:00, a goal
the operator asked for) failed three times at 22:00/22:05/22:11 against
`AllProvidersUnavailableError`, exhausted its three retries inside ten minutes, and
advanced to `next_run_at` a full day later. The provider came back that night. Nothing
ran it.

THE DEFAULT IS DIFFERENT FOR THE TWO KINDS OF JOB, which is why this is a writer and
not a flipped constant:

  * A **user-created** recurring goal (`created_by=cronjob` — four live rows, the
    daily@17:00 and two daily@09:00 goals) missed inside the window is the same data
    loss the one-shot branch already refuses to accept: "deferring it is data loss,
    not a benign reschedule".
  * A **platform sweep** seeded by `assembly.py` runs every few minutes. Replaying a
    missed tick buys nothing, so those keep the default and this test pins that they
    do — the distinction has to stay deliberate.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_CRONJOB = _ROOT / "src" / "stackowl" / "tools" / "scheduling" / "cronjob.py"
_ASSEMBLY = _ROOT / "src" / "stackowl" / "scheduler" / "assembly.py"


def _create_job_calls(path: Path) -> list[dict[str, ast.expr]]:
    """Every `create_job(...)` call in *path*, as its keyword mapping.

    Read from the AST rather than by grepping: the defect is a keyword that is
    ABSENT, and absence is exactly what a text search cannot see.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[dict[str, ast.expr]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name == "create_job":
            out.append({kw.arg: kw.value for kw in node.keywords if kw.arg})
    return out


def _is_true(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


class TestTheFlagHasAWriter:
    @pytest.mark.tripwire
    def test_every_user_created_job_opts_into_replay(self) -> None:
        """THE MISSING WRITER. Without this the flag can only ever be False."""
        calls = _create_job_calls(_CRONJOB)

        assert calls, "no create_job call found in cronjob.py — the walk is broken"
        missing = [i for i, kws in enumerate(calls) if not _is_true(kws.get("replay_missed"))]
        assert not missing, (
            "these cronjob create_job calls do not set replay_missed=True, so a goal "
            f"the user asked for is silently skipped when its slot is missed: {missing}"
        )


class TestASweepKeepsTheDefault:
    def test_platform_seeded_jobs_do_not_opt_in(self) -> None:
        """The control. If everything replayed, the flag would carry no information
        and a missed 5-minute sweep would re-run for no reason.

        THE FIRST VERSION OF THIS TEST WAS VACUOUS and the mutation attempt is what
        exposed it: it iterated `create_job(...)` calls in `assembly.py`, and there
        are NONE — the seeding path goes through `_seed_daily_schedule`, which uses
        `insert_job` or a short insert. So it asserted over an empty list and could
        never fail. This asserts the file does not mention the flag at all, which is
        a claim the file can actually violate.
        """
        assembly = _ASSEMBLY.read_text(encoding="utf-8")

        assert len(assembly) > 10_000, "assembly.py did not load — the control is blind"
        assert "replay_missed" not in assembly, (
            "a platform-seeded job now sets replay_missed; its next tick is minutes "
            "away, so replaying a missed one buys nothing and the flag stops "
            "distinguishing the two kinds of job"
        )

    def test_the_reader_still_gates_on_the_flag(self) -> None:
        """VACUITY CONTROL. If `recover` stopped consulting `replay_missed`, the
        writer above would be decoration in the other direction."""
        scheduler = (_ROOT / "src" / "stackowl" / "scheduler" / "scheduler.py").read_text(
            encoding="utf-8"
        )

        assert "job.replay_missed or not self._is_recurring(job)" in scheduler
