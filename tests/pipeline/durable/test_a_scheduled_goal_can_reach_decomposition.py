"""The decomposition path has never fired, and a scheduled goal cannot reach it.

MEASURED 2026-08-31, after Bakir was paged for a goal_execution job that failed
three times in twenty minutes::

    22:04  budget:stop:tokens  limit 500,000  actual 550,057
    22:12  budget:stop:tokens  limit 500,000  actual 506,496
    22:22  budget:stop:tokens  limit 500,000  actual 506,106

1.75M input tokens on the Daily Gmail digest, three identical attempts, and the
mechanism built for exactly this never ran. Across every retained log there is not
one ``task RESHAPED`` record — the six matches for "decompos" are all
``schedule_commit_classifier`` and none is a decomposition.

WHY IT CANNOT FIRE. ``wants_reshaping("budget")`` is True and the whole path behind
it is correct — ``should_decompose`` -> ``plan_subtasks`` -> children +
``set_dependencies``. The gate is::

    if task.attempt_count < DECOMPOSE_AFTER_ATTEMPTS:   # 3
        return False

and each of the three Gmail tasks carries ``attempt_count = 1``. They are three
SEPARATE tasks, not three attempts of one: ``task_runner.run`` mints
``task-{uuid4().hex[:12]}`` on every call, and the scheduler's job-level retry calls
it again. So the counter resets to 1 each time and never reaches 3.

The reshaping ladder counts a ROW's attempts; a scheduled goal retries by minting a
new ROW. Built, correct, and structurally unreachable for the case it exists for.

THE FIX COUNTS THE GOAL, NOT THE ROW. The goal text is stable for a scheduled job —
it comes from the job's params — so "how many times has this exact work already
failed for a reason repetition cannot fix" is answerable from the tasks table with
no new column and no change to task identity.

CONSERVATIVE IN THE SAME DIRECTION AS BEFORE. ``should_decompose``'s own docstring:
"False in every doubtful case. A wrong 'no' costs one more ordinary retry; a wrong
'yes' spends an LLM call and multiplies the row into a fan-out." Only failures whose
class actually wants reshaping are counted, so an ordinary flaky task does not
accumulate toward a split it has no use for.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.durable.decompose import (
    DECOMPOSE_AFTER_ATTEMPTS,
    should_decompose,
)


class _Task:
    """Only the fields the gate reads."""

    def __init__(
        self, *, attempt_count: int = 1, depends_on: tuple = (),
        parent_task_id: str | None = None, goal: str = "Daily Gmail digest",
    ) -> None:
        self.attempt_count = attempt_count
        self.depends_on = depends_on
        self.parent_task_id = parent_task_id
        self.goal = goal


def test_the_live_case_three_SEPARATE_tasks_now_decompose() -> None:
    """The Gmail digest: attempt_count 1 every time, because each retry is a new
    row. Counting the goal's history is what makes the third one reachable."""
    task = _Task(attempt_count=1)

    assert should_decompose(task, prior_failures=2) is True


def test_the_ROW_counter_still_works_on_its_own() -> None:
    """A task that really did retry in place must behave exactly as before."""
    assert should_decompose(_Task(attempt_count=DECOMPOSE_AFTER_ATTEMPTS)) is True
    assert should_decompose(_Task(attempt_count=DECOMPOSE_AFTER_ATTEMPTS - 1)) is False


def test_a_FIRST_failure_never_decomposes() -> None:
    """The expensive direction. A wrong 'yes' spends an LLM call and fans the row
    out; one bad turn is not evidence that the shape is wrong."""
    assert should_decompose(_Task(attempt_count=1), prior_failures=0) is False
    assert should_decompose(_Task(attempt_count=1), prior_failures=1) is False


def test_an_ALREADY_SPLIT_task_is_never_split_again() -> None:
    """Unchanged guard: each child would fail, split, and its children split."""
    task = _Task(attempt_count=9, depends_on=("c1",))
    assert should_decompose(task, prior_failures=9) is False


def test_a_CHILD_is_never_split() -> None:
    """Unchanged depth ceiling — a sub-task is meant to BE the simple half."""
    task = _Task(attempt_count=9, parent_task_id="p1")
    assert should_decompose(task, prior_failures=9) is False


def test_a_caller_that_passes_no_history_falls_back_to_the_row() -> None:
    """`prior_failures` defaults to 0, so every existing caller — and every test in
    this suite that predates it — behaves exactly as before."""

    class _Old:
        attempt_count = 1
        depends_on = ()
        parent_task_id = None
        goal = "x"

    assert should_decompose(_Old()) is False
    _Old.attempt_count = DECOMPOSE_AFTER_ATTEMPTS
    assert should_decompose(_Old()) is True


def test_the_two_counts_are_COMBINED_not_either_or() -> None:
    """One in-row attempt plus two earlier rows for the same goal is three failures
    of the same work, which is the threshold's actual meaning."""
    assert should_decompose(_Task(attempt_count=2), prior_failures=1) is True
