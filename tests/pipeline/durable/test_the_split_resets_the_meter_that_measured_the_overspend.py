"""The mechanism that fires BECAUSE a task overspent resets the meter that measured it.

MEASURED 2026-09-10 on the live database. `task-89b6c47ac995` was seeded at
**508,225** accumulated input tokens — it had breached the 500,000 cap, and that
breach is the `budget` failure class that makes `wants_reshaping` true in the first
place. Its NINE children each show `accumulated_input_tokens = 0`, with attempt
counts 12, 12, 4, 3, 2, 1, 1, 1. Two of those lineages burned 117.9 and 84.9
minutes of model time.

Over the two days to 2026-09-10 `jobmarket` took **483.8 minutes — 8.06 hours, 82%
of ALL turn time across every owl** — and 71% of its turns were retries.

WHY IT WAS INVISIBLE, and this is the root cause rather than the arithmetic:
`accumulated_input_tokens` and `accumulated_cost_usd` are COLUMNS ON `tasks` WITH NO
FIELD ON `DurableTask`. They are reachable only through four store methods, so every
constructor in this tree — including `plan_subtasks`, which enumerates its inherited
fields one by one and carries an explicit comment about why `destination` must be
among them — cannot see them. They were not forgotten. They were never visible to be
forgotten, and a new row simply takes the column's `DEFAULT 0`.

The same shape one level up is already recorded in `steps/execute.py`: a goal spent
4,172,113 input tokens over nine traces, 66% of a day's bill, and "cost was immune
the whole time because cost keys on the task". That fix keyed the meter to
`task_id`. Decomposition mints new task ids.

WHAT THIS DOES NOT DO: inherit the total. The parent breached the cap BEFORE
deciding to split, so a child inheriting 508,225 against a 500,000 cap is dead on
arrival and decomposition becomes a no-op. Inheriting is as wrong as resetting, and
what a split ask should be allowed to spend is a product decision — ESC-168.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from stackowl.pipeline.durable.decompose import _NOT_INHERITED_BY_A_CHILD  # noqa: PLC2701

_SRC = Path(__file__).resolve().parents[3] / "src" / "stackowl"
_MIGRATIONS = _SRC / "db" / "migrations"


def _accumulated_columns_on_tasks() -> set[str]:
    """Every `accumulated_*` column any migration adds to `tasks`.

    DERIVED, not listed — the lesson DEBT-291 paid for a day earlier: a set pinned
    to the migrations somebody remembered cannot detect the schema moving past it.
    """
    found: set[str] = set()
    pattern = re.compile(
        r"ALTER\s+TABLE\s+tasks\s+ADD\s+COLUMN\s+(accumulated_\w+)", re.IGNORECASE
    )
    comment = re.compile(r"--[^\n]*")
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        sql = comment.sub("", path.read_text(encoding="utf-8"))
        found.update(m.group(1) for m in pattern.finditer(sql))
    return found


@pytest.mark.tripwire
def test_every_cumulative_column_states_whether_a_child_inherits_it() -> None:
    """THE STRUCTURAL HALF, and the whole fix.

    A bounding column that lives only in the table is invisible to the code that
    builds tasks — which is exactly how two of them came to be silently reset. The
    next one cannot: it must be either a field the child is given, or named here
    with a reason.
    """
    columns = _accumulated_columns_on_tasks()
    assert columns, "the scan found no accumulated_* columns — the walk is broken"

    from stackowl.pipeline.durable.task import DurableTask

    inherited = {f for f in getattr(DurableTask, "__dataclass_fields__", {})}
    unaccounted = columns - inherited - set(_NOT_INHERITED_BY_A_CHILD)
    assert not unaccounted, (
        f"{sorted(unaccounted)} bound a task's cumulative spend and nothing says "
        "whether a child inherits them. A column with no field on DurableTask is "
        "invisible to every constructor, which is how the first two were reset "
        "without anyone deciding to."
    )


@pytest.mark.tripwire
def test_each_stated_reset_carries_a_reason_and_names_the_escalation() -> None:
    """A declaration with no reason is a list, and a list rots. Each entry has to
    say WHY the reset is right, and the one that is a product decision has to point
    at the decision."""
    assert _NOT_INHERITED_BY_A_CHILD, "the declaration was emptied"
    for column, reason in _NOT_INHERITED_BY_A_CHILD.items():
        assert len(reason) > 40, f"{column} has a reason too short to be one: {reason!r}"
    joined = " ".join(_NOT_INHERITED_BY_A_CHILD.values())
    assert "ESC-168" in joined, (
        "the reset that is a PRODUCT decision no longer points at the decision — "
        "an escalation nobody can find from the code is an escalation nobody answers"
    )


@pytest.mark.tripwire
def test_the_declaration_matches_what_a_child_is_actually_given() -> None:
    """THE CONTROL. The declaration says these are NOT inherited; if `plan_subtasks`
    ever started passing one, the declaration would be a lie that reads as a
    decision. Read from the AST of the child construction, not from prose."""
    from stackowl.pipeline.durable import decompose

    tree = ast.parse(Path(inspect.getfile(decompose)).read_text(encoding="utf-8"))
    passed: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "DurableTask":
            passed.update(kw.arg for kw in node.keywords if kw.arg)
    assert passed, "no DurableTask(...) construction found — the walk is broken"
    overlap = passed & set(_NOT_INHERITED_BY_A_CHILD)
    assert not overlap, (
        f"{sorted(overlap)} is declared NOT inherited and is being passed to the "
        "child — the declaration and the code disagree"
    )
    # And the fields the delivery contract depends on ARE still passed, so this
    # test cannot pass by the construction losing its arguments.
    assert {"destination", "channel", "session_key"} <= passed


@pytest.mark.tripwire
def test_the_reset_is_announced_where_it_happens_at_INFO() -> None:
    """Production writes zero DEBUG. A reset nobody can see in the log is a reset
    nobody can cost, which is how 8.06 hours of one owl's retries went unexplained.
    """
    from stackowl.pipeline.durable import loop

    tree = ast.parse(Path(inspect.getfile(loop)).read_text(encoding="utf-8"))
    level: str | None = None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        text = "".join(
            a.value for a in node.args
            if isinstance(a, ast.Constant) and isinstance(a.value, str)
        )
        if "RESET the cumulative token budget" in text:
            level = node.func.attr
    assert level is not None, (
        "the split no longer says that it reset the budget — the fact returns to "
        "being true and invisible"
    )
    assert level in {"info", "warning", "error"}, f"announced at {level}"
