"""Deleting the parent and leaving the children is how 36 MB accumulated.

``side_effect_ledger`` is the exactly-once record for a durable task: a row says
"step K of task T already committed, here is what it returned", so a replay skips
the side effect instead of repeating it. Its whole meaning is scoped to a task.

MEASURED 2026-09-03 on the live database (343 MB total):

    side_effect_ledger        36.1 MB   1,968 rows   ~18 KB/row
      of which tool='shell'   34.2 MB   1,076 rows   largest single row 3.4 MB

    ledger rows by the status of the task they belong to
      task no longer exists   1,284 rows    9.0 MB    <- 65%
      failed                    672 rows   26.1 MB
      dead_letter                 9 rows
      completed                   3 rows

NOT ONE ROW BELONGS TO A LIVE TASK, and 65% belong to no task at all. A row whose
task_id is absent from ``tasks`` can never be replayed — there is nothing left to
resume — so those nine megabytes are unreachable by construction.

THE WRITER OF THOSE ORPHANS IS ``prune_completed``. It deletes completed,
delivered tasks past a one-day window — Bakir asked for it explicitly — and says
carefully what it does NOT touch ("the per-turn learning corpus lives in
task_outcomes, a separate table this never touches"). It does not mention the
ledger, and it does not delete from it. The schema cannot help: ``task_id`` is
``NOT NULL`` but carries no FOREIGN KEY, so nothing cascades.

THE CAUSE IS THE ONE THIS PROGRAMME ALREADY NAMES, POINTING THE OTHER WAY. The
recorded rule is "remove the WRITER, not just the rows" — a row deleted while its
writer lives is re-seeded. Here the parent is deleted while its children live, and
they are never re-read, never re-written, and never removed. Same missing link,
opposite direction, and it only appends.

WHY THE FAILED ROWS ARE DELIBERATELY LEFT ALONE. 849 failed tasks still hold
retry budget (attempt_count 0 against max_attempts 30). A retry of the SAME
task_id is exactly when the ledger must still answer — deleting those rows would
break the exactly-once guarantee this table exists to provide. Only tasks being
deleted anyway lose their ledger rows, in the same transaction that deletes them.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.ledger import SideEffectLedger
from stackowl.pipeline.durable.store import DurableTaskStore
from tests.pipeline.durable.test_one_loop_store import _pending, store  # noqa: F401

pytestmark = pytest.mark.asyncio


async def _ledger_rows(db: DbPool, task_id: str) -> int:
    rows = await db.fetch_all(
        "SELECT COUNT(*) AS n FROM side_effect_ledger WHERE task_id = ?", (task_id,),
    )
    return int(rows[0]["n"])


async def _commit_a_side_effect(db: DbPool, task_id: str, step: int = 0) -> None:
    """Open and commit one exactly-once row, through the ledger's own API."""
    ledger = SideEffectLedger(db, "principal-default")
    args = {"cmd": "ls"}
    await ledger.begin(
        task_id=task_id, step_index=step, tool_name="shell", args=args,
    )
    await ledger.commit(
        task_id=task_id, step_index=step, tool_name="shell", args=args,
        result="a" * 64,
    )


# --------------------------------------------------------------------------- #
# The regression                                                               #
# --------------------------------------------------------------------------- #


async def test_pruning_a_task_removes_its_ledger_rows(store: DurableTaskStore) -> None:
    """THE DEFECT. 1,284 live rows — 65% of the table — belong to a task_id that
    is not in ``tasks``, because this prune deleted the task and left them."""
    await _pending(store, "old")
    await _commit_a_side_effect(store._db, "old")  # noqa: SLF001 — same seam the suite uses
    assert await _ledger_rows(store._db, "old") == 1  # noqa: SLF001

    await store.mark_delivered("old", result="done")
    await store._db.execute(  # noqa: SLF001 — age it past the window
        "UPDATE tasks SET updated_at = datetime('now','-2 day') WHERE task_id='old'"
    )

    pruned = await store.prune_completed(older_than_days=1)

    assert pruned == 1
    assert await _ledger_rows(store._db, "old") == 0, (  # noqa: SLF001
        "the task is gone and its exactly-once rows remain — unreachable forever, "
        "because a replay needs the task that no longer exists"
    )


async def test_a_task_that_is_NOT_pruned_keeps_its_ledger(
    store: DurableTaskStore,
) -> None:
    """THE HALF THAT MUST NOT MOVE. A ledger row is the exactly-once guarantee;
    removing one for a task still on the loop would let a replay repeat a side
    effect that already happened."""
    await _pending(store, "fresh")
    await _commit_a_side_effect(store._db, "fresh")  # noqa: SLF001
    await store.mark_delivered("fresh", result="done")

    await store.prune_completed(older_than_days=1)  # too recent to prune

    assert (await store.get("fresh")).task_id == "fresh"
    assert await _ledger_rows(store._db, "fresh") == 1  # noqa: SLF001


async def test_a_FAILED_task_keeps_its_ledger(store: DurableTaskStore) -> None:
    """849 live failed tasks still hold retry budget, and a retry reuses the
    task_id — which is exactly when the ledger must still answer. The prune is
    scoped to 'completed' and this asserts the ledger cleanup inherits that scope
    rather than widening it."""
    await _pending(store, "broken", max_attempts=30)
    await _commit_a_side_effect(store._db, "broken")  # noqa: SLF001
    await store.fail_and_requeue("broken", error="x", failure_class="transient")
    await store._db.execute(  # noqa: SLF001
        "UPDATE tasks SET updated_at = datetime('now','-9 day') WHERE task_id='broken'"
    )

    await store.prune_completed(older_than_days=1)

    assert await _ledger_rows(store._db, "broken") == 1, (  # noqa: SLF001
        "a retryable task lost its exactly-once record — the next attempt would "
        "repeat a side effect that already committed"
    )


async def test_one_task_does_not_take_another_tasks_rows(
    store: DurableTaskStore,
) -> None:
    """The delete must be scoped to the rows being pruned. A DELETE that forgot
    its predicate would silently destroy every exactly-once guarantee at once."""
    await _pending(store, "gone")
    await _pending(store, "staying")
    await _commit_a_side_effect(store._db, "gone")  # noqa: SLF001
    await _commit_a_side_effect(store._db, "staying")  # noqa: SLF001
    await store.mark_delivered("gone", result="done")
    await store._db.execute(  # noqa: SLF001
        "UPDATE tasks SET updated_at = datetime('now','-2 day') WHERE task_id='gone'"
    )

    await store.prune_completed(older_than_days=1)

    assert await _ledger_rows(store._db, "gone") == 0  # noqa: SLF001
    assert await _ledger_rows(store._db, "staying") == 1  # noqa: SLF001
