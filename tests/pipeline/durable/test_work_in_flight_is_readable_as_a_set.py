"""Work in flight, as a set — and why a row is not moving. A05.6.

The gap: work is visible ONE ROW AT A TIME and only to somebody who already
knows the id. `task_status` takes a REQUIRED `task_id`, so "what is running" has
no query at all.

**THE REASON COLUMN IS THE ITEM, NOT THE LIST.** MEASURED 2026-09-12 on the live
database: five `secretary` rows sit `pending` with `next_attempt_at` TEN HOURS in
the past, `last_error` set, attempt 1-2 of 30, no lease — and `claimable()`
returns NOTHING, correctly. Every one is a sub-task whose parent is terminal, and
the loop deliberately will not run a child of a terminal parent. A set view that
showed only status would have reported a wedged platform, the operator would have
restarted, and the five would still be there. **Inventing an alarm is worse than
the silence it replaces**, which is why `blocked` is pinned here rather than
treated as a nicety.

AND THE FIRST DESIGN WAS DISQUALIFIED BY READING THE CODE, not by taste: it
called `DurableTaskStore.claimable()` and marked everything absent from the
result as blocked. `claimable()` WRITES — `_deps_satisfied` issues
`UPDATE ... SET status='dead_letter'` when a dependency has failed. A GET route
calling it would let a READ-only principal dead-letter tasks by refreshing a
dashboard. So the SQL half of the predicate is shared as `store.CLAIMABLE_WHERE`
and the write-capable dependency half stays where it is.
"""

from __future__ import annotations

import ast
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests._schema_template import seed_schema

from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.activity import read_task_activity
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

_NOW = datetime(2026, 9, 12, 1, 0, tzinfo=UTC)


def _reader_ast() -> ast.Module:
    """The reader module as a tree — so a guard can ask about CODE."""
    import inspect

    from stackowl.pipeline.durable import activity as mod

    return ast.parse(inspect.getsource(mod))


def _reader_literals() -> list[str]:
    """Every string constant in the reader EXCEPT the docstrings.

    `ast.get_docstring` covers the module and each def; a constant that is the
    sole expression of its own statement is a docstring by position, which is
    what `ast.Expr(Constant)` detects.
    """
    tree = _reader_ast()
    doc_nodes = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
        and isinstance(n.body[0].value.value, str)
    }
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in doc_nodes
    ]


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "a056.db"
    seed_schema(db_path)
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def _row(pool: DbPool, task_id: str, **over: object) -> None:
    cols: dict[str, object] = {
        "task_id": task_id,
        "owner_id": DEFAULT_PRINCIPAL_ID,
        "goal": "g",
        "status": "pending",
        "current_step": 0,
        "created_at": _NOW.isoformat(),
        "updated_at": _NOW.isoformat(),
        "attempt_count": 1,
        "max_attempts": 30,
    }
    cols.update(over)
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    await pool.execute(
        f"INSERT INTO tasks ({names}) VALUES ({marks})",  # noqa: S608
        tuple(cols.values()),
    )


async def _read(pool: DbPool) -> dict[str, str]:
    rows, _ = await read_task_activity(
        pool, owner_id=DEFAULT_PRINCIPAL_ID, now_iso=_NOW.isoformat()
    )
    return {r.task_id: r.blocked for r in rows}


class TestItSaysWhyARowIsNotMoving:
    @pytest.mark.tripwire
    async def test_a_pending_child_of_a_TERMINAL_parent_is_not_reported_as_stuck(
        self, pool: DbPool
    ) -> None:
        """The live shape, reproduced. Without `blocked` this row is
        indistinguishable from a wedged loop."""
        await _row(pool, "parent", status="completed")
        await _row(
            pool, "child", parent_task_id="parent",
            next_attempt_at=(_NOW - timedelta(hours=10)).isoformat(),
            last_error="the last attempt ran out of tokens",
        )

        assert (await _read(pool))["child"] == "terminal_parent"

    @pytest.mark.tripwire
    async def test_a_row_the_loop_may_take_NOW_reads_none(self, pool: DbPool) -> None:
        """The vacuity control. Without it every branch could return
        `terminal_parent` and the test above would still pass."""
        await _row(pool, "ready", next_attempt_at=(_NOW - timedelta(minutes=1)).isoformat())

        assert (await _read(pool))["ready"] == "none"

    @pytest.mark.tripwire
    async def test_backoff_and_supersession_are_distinguished(self, pool: DbPool) -> None:
        await _row(pool, "waiting", next_attempt_at=(_NOW + timedelta(hours=1)).isoformat())
        await _row(pool, "gone", superseded=1)

        blocked = await _read(pool)
        assert blocked["waiting"] == "backoff"
        assert blocked["gone"] == "superseded"


class TestItAgreesWithTheLoop:
    @pytest.mark.tripwire
    async def test_the_rows_it_calls_unblocked_are_EXACTLY_the_ones_the_loop_claims(
        self, pool: DbPool
    ) -> None:
        """The one-source rule, proven rather than asserted.

        The surface and the engine share `CLAIMABLE_WHERE`, so they cannot drift
        on status, supersession, backoff or the terminal-parent rule — and this
        pins the agreement end to end, because a shared constant still leaves
        room for the reader to mis-evaluate it.
        """
        await _row(pool, "parent", status="completed")
        await _row(pool, "child", parent_task_id="parent")
        await _row(pool, "gone", superseded=1)
        await _row(pool, "waiting", next_attempt_at=(_NOW + timedelta(hours=1)).isoformat())
        await _row(pool, "ready")
        await _row(pool, "running_one", status="running")

        blocked = await _read(pool)
        unblocked = {tid for tid, why in blocked.items() if why == "none"}

        store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
        claims = {t.task_id for t in await store.claimable(limit=50, now=_NOW)}

        # `running_one` is not pending, so the reader reports `none` (the column
        # answers "why is this PENDING row not moving") while the loop does not
        # claim it. Compare on the pending population only, which is the set the
        # question is about.
        pending_unblocked = unblocked - {"running_one"}
        assert pending_unblocked == claims, (
            f"the surface and the loop disagree: surface says {sorted(pending_unblocked)} "
            f"may run, the loop claims {sorted(claims)}"
        )
        assert claims == {"ready"}, "the fixture stopped exercising a real claim"

    @pytest.mark.tripwire
    async def test_a_row_whose_DEPENDENCY_is_unmet_agrees_with_the_loop(
        self, pool: DbPool
    ) -> None:
        """A CONSTRUCTED population, because the live one cannot reach this.

        MEASURED 2026-09-12: four rows carry `depends_on` and ZERO are pending,
        so on today's data a reader that stopped at the SQL half would agree
        with the loop by accident and no live assertion could tell. The
        disagreement is real: `CLAIMABLE_WHERE` says yes, and `claimable()` then
        asks `_deps_satisfied` and skips the row.
        """
        await _row(pool, "blocker", status="running")
        await _row(pool, "needs_it", depends_on="blocker")
        await _row(pool, "free")

        blocked = await _read(pool)
        assert blocked["needs_it"] == "waiting_on_dependency", (
            "the reader stopped at the SQL half and called a dependency-blocked "
            "row runnable"
        )

        store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
        claims = {t.task_id for t in await store.claimable(limit=50, now=_NOW)}
        assert claims == {"free"}, claims
        assert {tid for tid, why in blocked.items() if why == "none"} - {"blocker"} == claims

    @pytest.mark.tripwire
    def test_the_predicate_is_ONE_string_not_two(self) -> None:
        """A copy nobody runs is the one that goes stale."""
        import inspect

        from stackowl.pipeline.durable import store as store_mod

        src = inspect.getsource(store_mod.DurableTaskStore.claimable)
        assert "CLAIMABLE_WHERE" in src, (
            "claimable() stopped using the shared constant — the surface can now "
            "drift from the loop"
        )
        assert "COALESCE(superseded, 0) = 0" not in src, (
            "the predicate was pasted back into claimable(); there are two copies "
            "again"
        )


class TestItIsSafeToServe:
    @pytest.mark.tripwire
    def test_the_reader_NEVER_calls_the_write_capable_claimable(self) -> None:
        """`claimable()` dead-letters a row whose dependency failed. A GET that
        called it would let a READ-only principal mutate task state by
        refreshing a dashboard.

        ASKED OF THE AST, NOT OF THE TEXT. The first version grepped
        `inspect.getsource(mod)` for `.claimable(` and failed on the module's own
        DOCSTRING, which explains at length why calling it would be wrong. A
        guard that cannot tell code from the documentation warning against that
        code is the prose-match this repo has now recorded six times — and it
        fails on correct work, which is how a guard gets deleted rather than
        satisfied.
        """
        calls = {
            n.func.attr
            for n in ast.walk(_reader_ast())
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "claimable" not in calls, (
            "the reader calls the write-capable claimable(): a GET can now "
            "dead-letter tasks"
        )

    @pytest.mark.tripwire
    def test_it_names_its_columns_and_never_selects_star(self) -> None:
        """`_fetch_owned` is `SELECT *` over a table holding 15.8 MB of
        `checkpoint_blob` across 1,211 rows. Pulled over and discarded.

        Over the module's STRING LITERALS with docstrings removed, for the same
        reason as above: this module's prose quotes both phrases while
        explaining why it avoids them.
        """
        sql = " ".join(_reader_literals())
        assert "SELECT *" not in sql, "the reader grew a SELECT *"
        assert "checkpoint_blob" not in sql, "the reader pulls the blob column"

    @pytest.mark.tripwire
    async def test_finished_work_is_excluded_and_user_content_is_EXCERPTED(
        self, pool: DbPool
    ) -> None:
        """History is not activity: 1,129 of 1,211 live rows are completed or
        failed. And `goal` is what somebody typed — a set view shows enough to
        recognise a row, never the whole thing."""
        await _row(pool, "done", status="completed")
        await _row(pool, "bad", status="failed")
        await _row(pool, "live", goal="x" * 400, last_error="y" * 400)

        rows, _ = await read_task_activity(
            pool, owner_id=DEFAULT_PRINCIPAL_ID, now_iso=_NOW.isoformat()
        )

        assert {r.task_id for r in rows} == {"live"}
        only = rows[0]
        assert len(only.goal) < 400 and only.goal.endswith("…")
        assert only.last_error is not None and len(only.last_error) < 400

    @pytest.mark.tripwire
    async def test_work_owned_by_another_principal_is_COUNTED_not_shown(
        self, pool: DbPool
    ) -> None:
        """MEASURED: only 896 of 1,211 rows carry `principal-default`. An
        owner-scoped read is missing a quarter of the table unless it says so."""
        await _row(pool, "mine")
        await _row(pool, "theirs", owner_id="someone-else")

        rows, gaps = await read_task_activity(
            pool, owner_id=DEFAULT_PRINCIPAL_ID, now_iso=_NOW.isoformat()
        )

        assert {r.task_id for r in rows} == {"mine"}
        assert gaps.unseen_other_owner == 1
        assert gaps.truncated is False


class TestTheLifecycleVocabularyIsTheSOURCE:
    """The cause, pinned — and it is one word old, not one mistake.

    MEASURED 2026-09-12. `dead_letter` arrived with migration 0119, after
    `completed`/`failed` had been the terminal pair for months, so everyone who
    wrote the terminal set from memory wrote TWO words. FOUR live instances:

    * this module's set SQL — the defect, `GET /api/v1/tasks` served **82 rows
      where 5 were live**, 72 of the 77 `dead_letter` rows created in one batch
      on 2026-08-20, twenty-three days earlier;
    * this module's `unseen_other_owner` SQL — the DENOMINATOR, which would then
      have counted other owners' dead letters against a set that drops mine;
    * `store._ACTIVE_TASK_STATUSES`'s comment — right tuple, wrong rationale,
      correct only because it is a COMPLEMENT rather than a list;
    * the page's own note to the operator — "completed and failed rows are
      history", which described the filter accurately and the intent wrongly.

    AND THE TEST ABOVE COULD NOT HAVE CAUGHT IT, which is the part worth keeping:
    `test_finished_work_is_excluded_and_user_content_is_EXCERPTED` seeds
    `completed` and `failed` — the SAME two words as the filter it checks, so it
    agreed with the bug by construction and would have gone on agreeing with it.
    It is left as it stands, because its other half (excerpting user content) is
    a real property worth keeping; the exclusion half is superseded below by one
    that walks `get_args(TaskStatus)` instead of naming anything.
    """

    @pytest.mark.tripwire
    def test_the_task_lifecycle_is_a_TOTAL_partition(self) -> None:
        """Every status is live or terminal, never neither and never both.

        Deliberately NOT derived one from the other. A complement would give an
        eighth status a silent default, and the two defaults fail in OPPOSITE
        directions: defaulting to terminal hides live work from the only surface
        that shows it. This makes an eighth word a decision somebody has to write
        down.
        """
        from typing import get_args

        from stackowl.pipeline.durable.task import (
            LIVE_TASK_STATUSES,
            TERMINAL_TASK_STATUSES,
            TaskStatus,
        )

        declared = set(get_args(TaskStatus))
        live, terminal = set(LIVE_TASK_STATUSES), set(TERMINAL_TASK_STATUSES)
        assert not (live & terminal), f"classified twice: {sorted(live & terminal)}"
        unknown = (live | terminal) - declared
        assert not unknown, f"classified a status TaskStatus does not declare: {sorted(unknown)}"
        missing = declared - live - terminal
        assert not missing, (
            f"TaskStatus gained {sorted(missing)} and neither group claims it. Decide "
            "in writing whether the loop is finished with it: a status in neither is "
            "counted LIVE by nothing and TERMINAL by nothing, and every reader that "
            "hand-lists one of the sets will disagree about it."
        )

    @pytest.mark.tripwire
    def test_no_reader_hand_lists_the_terminal_set(self) -> None:
        """Structural, because the instance is never the point.

        A predicate spelled out in SQL text cannot be updated by adding a status
        to the vocabulary, and this tree has now paid for that four times across
        one file pair.

        IT ASKS THE AST, NOT THE TEXT, AND THE FIRST VERSION DID NOT — it read
        lines and failed on the COMMENTS this change added to explain the defect,
        which is the fifth guard-matches-prose instance recorded this session
        after the owner-scope detector, the done-validate check, `_MARKUP_IDS`
        and `test_NEITHER_new_handler_reads_instance_state`. The cure has been
        the same every time: ask the code. `#` comments are not in the tree at
        all, and a docstring is the one string constant that opens a body, so
        both drop out and what remains is a string some statement actually uses.
        """
        import ast
        import re

        src = Path(__file__).resolve().parents[3] / "src" / "stackowl"
        pattern = re.compile(
            r"""NOT\s+IN\s*\(\s*['"]completed['"]\s*,\s*['"]failed['"]\s*\)"""
        )
        offenders: list[str] = []
        for path in sorted(src.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            docstrings = {
                id(node.body[0].value)
                for node in ast.walk(tree)
                if isinstance(
                    node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
                )
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            }
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in docstrings
                    and pattern.search(node.value)
                ):
                    offenders.append(f"{path.relative_to(src)}:{node.lineno}")
        assert not offenders, (
            "these predicates name two of the three terminal statuses, so a "
            f"dead-lettered row reads as live work: {offenders}"
        )

    def test_the_detector_reads_CODE_and_not_the_prose_beside_it(self) -> None:
        """Two controls in one, because this guard has both failure modes.

        Without the first, "no offenders" cannot be told apart from a regex that
        matches nothing. Without the second, the guard is the one that already
        fired on its own explanation.
        """
        import ast
        import re

        pattern = re.compile(
            r"""NOT\s+IN\s*\(\s*['"]completed['"]\s*,\s*['"]failed['"]\s*\)"""
        )
        assert pattern.search("\"AND status NOT IN ('completed', 'failed') \"")
        assert pattern.search("AND status NOT IN ('completed','failed')")
        assert not pattern.search(
            "AND status NOT IN ('completed','failed','dead_letter','parked')"
        )

        # The prose half: a comment and a docstring carrying the exact spelling
        # must BOTH be invisible, while a real statement using it is not.
        module = ast.parse(
            '"""A docstring naming NOT IN (\'completed\', \'failed\') on purpose."""\n'
            "# a comment naming NOT IN ('completed', 'failed') too\n"
            "q = \"SELECT 1\"\n"
        )
        docstrings = {
            id(n.body[0].value)
            for n in ast.walk(module)
            if isinstance(n, ast.Module) and n.body
            and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
        }
        hits = [
            n for n in ast.walk(module)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings and pattern.search(n.value)
        ]
        assert not hits, "the detector still reads prose"

    @pytest.mark.tripwire
    async def test_EVERY_terminal_status_is_excluded_and_EVERY_live_one_is_kept(
        self, pool: DbPool
    ) -> None:
        """Driven from the vocabulary, not from a list written here.

        One row per declared status, so the assertion is about the PARTITION and
        not about the three words that happen to be terminal today. This is the
        test the original could not be: seeding `completed` and `failed` agrees
        with a filter that names `completed` and `failed`.
        """
        from typing import get_args

        from stackowl.pipeline.durable.task import LIVE_TASK_STATUSES, TaskStatus

        for status in get_args(TaskStatus):
            await _row(pool, f"row-{status}", status=status)

        rows, _ = await read_task_activity(
            pool, owner_id=DEFAULT_PRINCIPAL_ID, now_iso=_NOW.isoformat()
        )

        assert {r.task_id for r in rows} == {f"row-{s}" for s in LIVE_TASK_STATUSES}, (
            "the reader disagrees with the lifecycle partition about which rows are live"
        )

    @pytest.mark.tripwire
    async def test_what_the_filter_took_out_is_COUNTED_and_DATED(
        self, pool: DbPool
    ) -> None:
        """Removing rows from the set must not remove them from the operator.

        `dead_letter` is the one ending this platform promises never to prune, and
        it WAS in this view. So it is reported as a count with its recency — and
        the recency is the half that matters: a bare 77 reads as a crisis, while
        "77, newest three weeks ago" reads as the history it is. That difference
        is the entire defect this change repairs.
        """
        await _row(pool, "live")
        await _row(pool, "old", status="dead_letter", updated_at="2026-08-20T00:00:00+00:00")
        await _row(pool, "recent", status="dead_letter", updated_at="2026-09-11T15:09:11+00:00")

        rows, gaps = await read_task_activity(
            pool, owner_id=DEFAULT_PRINCIPAL_ID, now_iso=_NOW.isoformat()
        )

        assert {r.task_id for r in rows} == {"live"}
        assert gaps.dead_lettered == 2
        assert gaps.newest_dead_letter_at is not None
        assert gaps.newest_dead_letter_at.startswith("2026-09-11"), (
            "the newest dead letter is not reported, so the count cannot be read as "
            f"history rather than as a crisis: {gaps.newest_dead_letter_at}"
        )

    @pytest.mark.tripwire
    async def test_the_DENOMINATOR_measures_the_same_population_as_the_set(
        self, pool: DbPool
    ) -> None:
        """`unseen_other_owner` answers "what am I not seeing" and has to mean the
        same thing the set means. It carried the two-word predicate too, so it
        would have counted another owner's dead letters against a set that drops
        mine — a denominator over a different population from its numerator.
        """
        await _row(pool, "mine")
        await _row(pool, "theirs-live", owner_id="someone-else")
        await _row(pool, "theirs-dead", owner_id="someone-else", status="dead_letter")

        rows, gaps = await read_task_activity(
            pool, owner_id=DEFAULT_PRINCIPAL_ID, now_iso=_NOW.isoformat()
        )

        assert {r.task_id for r in rows} == {"mine"}
        assert gaps.unseen_other_owner == 1, (
            "the unseen count includes another owner's terminal rows while the set "
            f"excludes mine: {gaps.unseen_other_owner}"
        )
