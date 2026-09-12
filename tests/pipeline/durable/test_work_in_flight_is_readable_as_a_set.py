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
