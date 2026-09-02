"""Nothing has ever bounded `job_runs`, and it is 19% of the database.

MEASURED 2026-09-02 on the live store: **252,905 rows, every one
`status='completed'`**, spanning 2026-06-02 to today — 45.1 MB of table plus
19.1 MB of idempotency index, **19% of a 342 MB database**. The largest single
contributor is `objective_driver` at 67,551 runs, firing every minute against a
table that has zero rows.

Recorded failure shape #4: anything that only appends will poison its reader.

WHY DELETING OLD ROWS IS PROVABLY SAFE, which is the whole argument. `job_runs`
has exactly ONE reader — the exactly-once guard in `scheduler._dispatch`, which
looks up `idempotency_key`. That key is `_occurrence_key`:
`{job.idempotency_key}@{job.next_run_at}`, so it EMBEDS the scheduled instant,
and all 252,905 keys in the live table are distinct. Once an instant has passed
and the job has moved on, its key can never be queried again. There is no time
window in the guard that a retention could shorten.

THE WINDOW IS DELIBERATELY LOOSE, AND THAT IS NOT AN OVERSIGHT. At 100 days this
deletes ZERO rows today — the oldest row is 92 days old — while capping the table
for ever. The DEFECT is the unbounded append, and any bound fixes it. How tight
the bound should be is a data-deletion decision that belongs to the operator, and
"any data deletion" is a stop-and-brief item in this loop's own rules. The numbers
are on the table for him: 7 days would reclaim 88% of the rows and about 56 MB.

NOT A SECOND ENGINE. `db_reclaim` already runs hourly and already owns database
maintenance; retention runs there, before the incremental vacuum, because
`incremental_vacuum` can only hand back pages something has already freed.
"""

from __future__ import annotations

import pytest

from stackowl.scheduler.handlers.db_reclaim import (
    _RUN_HISTORY_RETENTION_DAYS,
    DbReclaimHandler,
)

pytestmark = pytest.mark.asyncio


class _Pool:
    def __init__(self, stale: int, fail: bool = False) -> None:
        self.stale = stale
        self.fail = fail
        self.executed: list[str] = []

    async def fetch_all(self, sql: str, params: tuple) -> list[dict]:
        if self.fail:
            raise RuntimeError("no such table")
        if "COUNT(*)" in sql and "job_runs" in sql:
            assert "ran_at <" in sql, "retention must be by age, not by count"
            return [{"n": self.stale}]
        return [{"n": 0}]

    async def execute(self, sql: str, params: tuple) -> None:
        if self.fail:
            raise RuntimeError("no such table")
        self.executed.append(sql)


async def test_it_deletes_only_rows_past_the_window() -> None:
    pool = _Pool(stale=734)
    n = await DbReclaimHandler(pool)._prune_run_history()  # noqa: SLF001
    assert n == 734
    deletes = [s for s in pool.executed if s.startswith("DELETE")]
    assert deletes, "nothing was deleted"
    assert all("ran_at <" in d and "job_runs" in d for d in deletes)


async def test_nothing_stale_means_no_DELETE_at_all() -> None:
    """An hourly job that issues a pointless DELETE every tick is churn on a
    table 250k rows deep."""
    pool = _Pool(stale=0)
    assert await DbReclaimHandler(pool)._prune_run_history() == 0  # noqa: SLF001
    assert not [s for s in pool.executed if s.startswith("DELETE")]


async def test_a_failure_never_costs_the_tick() -> None:
    """Maintenance may not fail a scheduler tick — the table simply stays
    unbounded until the next hour."""
    assert await DbReclaimHandler(_Pool(stale=5, fail=True))._prune_run_history() == 0  # noqa: SLF001


def test_the_window_is_the_one_the_operator_chose() -> None:
    """It shipped at 100 days first — deleting NOTHING, because "any data
    deletion" is a stop-and-brief item in this loop's rules and he had not decided
    it. He then chose 7 with the numbers in front of him: 223,266 of 255,363 rows
    (87%) are older than a week, worth about 56 MB of a 342 MB database.

    Pinned so a later reader does not drift it back without a decision."""
    assert _RUN_HISTORY_RETENTION_DAYS == 7


def test_a_huge_backlog_is_deleted_in_BATCHES() -> None:
    """A single DELETE over 223,266 rows holds SQLite's write lock for its whole
    duration, and this repo has already paid for database-is-locked events — one
    contention moment emits four. Each statement is bounded, so the pass takes
    several short locks instead of one long one."""
    import inspect

    from stackowl.scheduler.handlers import db_reclaim

    src = inspect.getsource(db_reclaim.DbReclaimHandler._prune_run_history)  # noqa: SLF001
    assert "LIMIT ?" in src, "the delete is unbounded again"
    assert "_PRUNE_MAX_PER_PASS" in src, "one pass can consume the whole backlog"


def test_retention_runs_BEFORE_the_vacuum() -> None:
    """Structural, and it is the ordering that makes the pass useful:
    `incremental_vacuum` can only hand back pages something has already freed, so
    pruning after it would leave them until the next hourly tick."""
    import inspect

    src = inspect.getsource(DbReclaimHandler.execute)
    # The CALL SITES, not the words: a first draft compared `src.index(
    # "incremental_vacuum")`, which matched the explanatory COMMENT sitting above
    # the prune call and failed a correct implementation. Matching prose instead
    # of code is the instrument error this project keeps paying for.
    assert (src.index("await self._prune_run_history()")
            < src.index("PRAGMA incremental_vacuum"))


def test_the_pruned_count_is_REPORTED() -> None:
    """A reclaimer that silently falls behind looks exactly like one that works —
    this file's sibling alert exists for that reason, and the same applies here."""
    import inspect

    src = inspect.getsource(DbReclaimHandler.execute)
    assert '"pruned_runs": pruned' in src
