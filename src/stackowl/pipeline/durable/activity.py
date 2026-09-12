"""Work in flight, as a SET — and why a row is not moving.

A05.6's gap: work is visible ONE ROW AT A TIME and only to somebody who already
knows the id. `task_status` takes a REQUIRED `task_id`, so "what is running" has
no query, and a running task, a retrying job and a wedged agent are
indistinguishable to anyone who cannot name the row first.

THE JOBS HALF WAS ALREADY SHIPPED. `GET /api/v1/schedules` (A05.4) returns every
job with its status, next run, failure count and last error. What remained is
TASKS as a set, which is what this reads.

**THE FIVE PENDING ROWS ARE WHY THIS CARRIES A REASON AND NOT JUST A STATUS.**
MEASURED 2026-09-12: five `secretary` tasks sit `pending` with `next_attempt_at`
TEN HOURS in the past, `last_error` set, attempt 1-2 of 30, no lease. Rendered
naively that is a wedged platform, and the operator's next move is a restart that
changes nothing. It is not wedged: every one is a sub-task whose PARENT is
terminal (`completed` x4, `dead_letter` x1), and the loop deliberately will not
run a child of a terminal parent — "a sub-task exists to serve its parent; once
that parent is terminal the child has no destination, no achievement and nobody
waiting". A surface that cannot say that INVENTS AN ALARM, which is worse than
the silence it replaces.

**AND THE OBVIOUS IMPLEMENTATION IS DISQUALIFIED, MEASURED RATHER THAN ARGUED.**
The first design called `DurableTaskStore.claimable()` and marked everything it
did not return as blocked. `claimable()` WRITES: its `_deps_satisfied` helper
issues `UPDATE ... SET status='dead_letter'` when a dependency has failed, and
logs a WARNING. A `GET` route calling it would let a READ-only principal
dead-letter tasks by refreshing a dashboard.

So the SQL half of the predicate is LIFTED into `store._CLAIMABLE_WHERE` and both
the loop and this reader consume that one constant — the surface cannot drift
from the engine on status, supersession, backoff or the terminal-parent rule. The
dependency half stays where it is, because resolving it is a write; a row whose
deps are unmet is reported `waiting_on_dependency` from a READ of `depends_on`,
and `test_the_reader_agrees_with_the_loop` pins the two answers equal.

NEVER `store.list()`. It goes through `_fetch_owned`, which is literally
`SELECT * FROM {table}` with no LIMIT, over a table holding **15.8 MB of
`checkpoint_blob` across 1,211 rows** (largest single blob 591 KB) — pulled over
the wire and discarded, since `DurableTask` has no such field. Columns are named
explicitly here for the reason `claimable()` already names them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.pipeline.durable.task import TERMINAL_TASK_STATUSES

#: Why a pending row is not moving. `none` means the loop may take it now.
BlockedReason = Literal[
    "none", "backoff", "terminal_parent", "superseded", "waiting_on_dependency"
]

#: How much of a stored error or goal reaches a surface. These are USER CONTENT —
#: a goal is what somebody typed and `last_error` can carry a model's reply — so
#: the set view shows enough to recognise a row and never the whole thing.
_EXCERPT = 160

#: Bound placeholders for the terminal set, so the STATUSES stay parameters and
#: never reach the statement as text. Built from the tuple, so adding a terminal
#: status cannot leave the placeholder count behind — the failure that shape
#: produces is a silent `sqlite3.ProgrammingError` at runtime, not at review.
_TERMINAL_MARKS = ", ".join("?" * len(TERMINAL_TASK_STATUSES))

#: Bounded by construction. `pending` is a status the loop DRAINS, so the live
#: population is small (5 today); the cap exists so a stuck loop cannot turn this
#: read into a table scan rendered as a web page.
DEFAULT_LIMIT = 200


@dataclass(frozen=True)
class TaskActivity:
    """One row of work, with the reason it is or is not moving.

    NO FUSED STATUS. `status` is the store's own column, verbatim; `blocked`
    answers a different question and is named separately. A04.2 will add an
    agent activation INSIDE a running row — a task can be `running` while the
    agent holding it is wedged — so collapsing the two into one word now would
    put a definition into `/api/v1` JSON that could never be split back out.
    """

    task_id: str
    owl_name: str | None
    status: str
    blocked: BlockedReason
    attempt_count: int
    max_attempts: int
    next_attempt_at: str | None
    lease_owner: str | None
    lease_expires_at: str | None
    goal: str
    last_error: str | None


@dataclass(frozen=True)
class TaskActivityGaps:
    """What this read could NOT see, so a surface can state its denominator.

    `unseen_other_owner` is not a rounding error: MEASURED 2026-09-12, only
    **896 of 1,211** task rows carry `principal-default`. 126 carry a raw
    numeric owner and the rest synthetic `owl:<name>:recovery:<id>` owners, so
    an owner-scoped read is silently missing a quarter of the table unless it
    says so.
    """

    unseen_other_owner: int
    truncated: bool

    #: WHAT THE TERMINAL FILTER TOOK OUT, so removing it from the set does not
    #: remove it from the operator's knowledge.
    #:
    #: `completed` and `failed` were never in this view and nobody expects them.
    #: `dead_letter` WAS — 77 of them, 72 created in one batch on 2026-08-20 —
    #: and it is the one ending this platform promises never to prune:
    #: `task_loop_settings` calls it "the one record" of work that ended without
    #: succeeding. So the count and its RECENCY are reported rather than the rows.
    #:
    #: The recency is the half that matters. "77 dead-lettered" reads as a crisis;
    #: "77 dead-lettered, newest 2026-09-11" reads as history with a date on it,
    #: and the difference is exactly the false impression the unfiltered list gave.
    dead_lettered: int
    newest_dead_letter_at: str | None


def _as_int(value: object) -> int:
    """A sqlite column arrives as `object`; a missing or NULL one means zero.

    Typed rather than cast inline so the reader cannot be handed a value that
    `int()` refuses and fail on a row instead of reporting it — a surface that
    500s on one odd row is worse than one that calls it unknown.
    """
    if value is None:
        return 0
    try:
        return int(value)  # type: ignore[call-overload,no-any-return]
    except (TypeError, ValueError):
        log.tasks.warning(
            "[tasks] activity.read: a numeric column was not numeric",
            extra={"_fields": {"value": repr(value)}},
        )
        return 0


def _excerpt(value: object) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= _EXCERPT else text[: _EXCERPT - 1] + "…"


async def read_task_activity(
    db: DbPool,
    *,
    owner_id: str,
    now_iso: str,
    limit: int = DEFAULT_LIMIT,
) -> tuple[list[TaskActivity], TaskActivityGaps]:
    """Unfinished work for *owner_id*, newest first, with a reason per row.

    UNFINISHED, not everything: `completed` and `failed` rows are history and
    there are 1,129 of them against 5 that matter. A set view whose first
    screen is last month's failures answers the wrong question.

    ONE STATEMENT, explicit columns, owner-scoped IN the statement. A JOIN
    scoping one side would pass `test_no_owner_scope_bypass` — it clears a
    statement when `owner_id` appears anywhere in it — so the fence cannot see
    that bug and this shape avoids being able to express it.
    """
    from stackowl.pipeline.durable.store import CLAIMABLE_WHERE

    t0 = time.monotonic()
    log.tasks.info(
        "[tasks] activity.read: entry",
        extra={"_fields": {"owner_id": owner_id, "limit": limit}},
    )

    rows = await db.fetch_all(
        "SELECT task_id, owl_name, status, attempt_count, max_attempts, "
        "next_attempt_at, lease_owner, lease_expires_at, goal, last_error, "
        "superseded, parent_task_id, depends_on, "
        # The loop's OWN eligibility predicate, evaluated per row rather than
        # re-implemented. `CLAIMABLE_WHERE` is the same string `claimable()`
        # runs, so status, supersession, backoff and the terminal-parent rule
        # cannot drift between what the surface SAYS and what the loop DOES.
        f"CASE WHEN {CLAIMABLE_WHERE} THEN 1 ELSE 0 END AS sql_eligible, "
        "(SELECT p.status FROM tasks p WHERE p.owner_id = tasks.owner_id "
        "   AND p.task_id = tasks.parent_task_id) AS parent_status "
        "FROM tasks WHERE owner_id = ? "
        # ASKS THE VOCABULARY. This read `NOT IN ('completed', 'failed')` and
        # served 82 rows where 5 were live — 72 of the 77 `dead_letter` rows it
        # carried were created on 2026-08-20, twenty-three days earlier, in one
        # batch. The docstring above calls that 'last month's failures' and says
        # it answers the wrong question; the filter simply named two of the three
        # terminal words, because `dead_letter` arrived after the other two.
        f"AND status NOT IN ({_TERMINAL_MARKS}) "
        "ORDER BY updated_at DESC LIMIT ?",
        (owner_id, now_iso, owner_id, owner_id, *TERMINAL_TASK_STATUSES,
         int(limit) + 1),
    )
    truncated = len(rows) > limit
    rows = rows[:limit]

    # THE SAME PREDICATE AS THE SET ABOVE, and it has to be. This counted
    # `NOT IN ('completed', 'failed')` while the set excluded three statuses, so
    # 'unseen' would have counted other owners' dead letters against a set that
    # drops mine — a denominator measuring a different population from its
    # numerator, which is the error this file's own gaps docstring exists for.
    unseen_rows = await db.fetch_all(
        f"SELECT COUNT(*) AS n FROM tasks WHERE owner_id <> ? "
        f"AND status NOT IN ({_TERMINAL_MARKS})",
        (owner_id, *TERMINAL_TASK_STATUSES),
    )
    unseen = int(unseen_rows[0]["n"]) if unseen_rows else 0

    # WHAT THE FILTER TOOK OUT, counted rather than shown. One statement, owner
    # scoped in the statement, and it asks for the NEWEST as well as the count —
    # a bare 77 reads as a crisis and `77, newest three weeks ago` reads as the
    # history it is. That difference is the whole defect this change repairs.
    dead_rows = await db.fetch_all(
        "SELECT COUNT(*) AS n, MAX(updated_at) AS newest FROM tasks "
        "WHERE owner_id = ? AND status = 'dead_letter'",
        (owner_id,),
    )
    dead_n = int(dead_rows[0]["n"] or 0) if dead_rows else 0
    dead_newest = str(dead_rows[0]["newest"]) if dead_rows and dead_rows[0]["newest"] else None

    # THE DEPENDENCY HALF, RESOLVED BY READING ONLY. `CLAIMABLE_WHERE` is the SQL
    # half of the loop's predicate and stops here: `claimable()` finishes in
    # Python via `_deps_satisfied`, which DEAD-LETTERS a row whose dependency
    # failed. So a row can satisfy the SQL and still be skipped by the loop, and
    # a reader that stopped at the SQL would report `none` for it — a
    # disagreement with the engine, on a surface whose whole job is to explain
    # what the engine is doing.
    #
    # MEASURED 2026-09-12: FOUR rows carry `depends_on` and ZERO of them are
    # pending, so this branch is unreachable on today's data and no live
    # assertion could pin it. It is pinned by a CONSTRUCTED population instead —
    # `test_a_row_whose_DEPENDENCY_is_unmet_agrees_with_the_loop`.
    dep_status = await _dependency_statuses(db, rows, owner_id=owner_id)

    out: list[TaskActivity] = []
    for r in rows:
        out.append(
            TaskActivity(
                task_id=str(r["task_id"]),
                owl_name=(str(r["owl_name"]) if r["owl_name"] else None),
                status=str(r["status"]),
                blocked=_blocked_reason(r, dep_status),
                attempt_count=int(r["attempt_count"] or 0),
                max_attempts=int(r["max_attempts"] or 0),
                next_attempt_at=(
                    str(r["next_attempt_at"]) if r["next_attempt_at"] else None
                ),
                lease_owner=(str(r["lease_owner"]) if r["lease_owner"] else None),
                lease_expires_at=(
                    str(r["lease_expires_at"]) if r["lease_expires_at"] else None
                ),
                goal=_excerpt(r["goal"]),
                last_error=(_excerpt(r["last_error"]) if r["last_error"] else None),
            )
        )

    gaps = TaskActivityGaps(
        unseen_other_owner=unseen,
        truncated=truncated,
        dead_lettered=dead_n,
        newest_dead_letter_at=dead_newest,
    )
    blocked_now = sum(1 for a in out if a.blocked != "none" and a.status == "pending")
    duration_ms = (time.monotonic() - t0) * 1000
    log.tasks.info(
        "[tasks] activity.read: exit — served",
        extra={"_fields": {
            "owner_id": owner_id,
            "unfinished": len(out),
            "blocked_pending": blocked_now,
            "unseen_other_owner": unseen,
            "truncated": truncated,
            "dead_lettered": dead_n,
            "duration_ms": duration_ms,
        }},
    )
    return out, gaps


async def _dependency_statuses(
    db: DbPool, rows: list[dict[str, object]], *, owner_id: str
) -> dict[str, str]:
    """Statuses of every id named in a `depends_on` on this page. READ ONLY.

    One statement for the whole page rather than one per row: `depends_on` is
    rare (4 rows today) but a per-row query would be an N+1 the day it is not.
    """
    wanted: set[str] = set()
    for r in rows:
        raw = r.get("depends_on")
        if raw:
            wanted.update(d.strip() for d in str(raw).split(",") if d.strip())
    if not wanted:
        return {}
    marks = ",".join("?" for _ in wanted)
    dep_rows = await db.fetch_all(
        f"SELECT task_id, status FROM tasks WHERE owner_id = ? AND task_id IN ({marks})",  # noqa: S608
        (owner_id, *sorted(wanted)),
    )
    return {str(d["task_id"]): str(d["status"]) for d in dep_rows}


def _blocked_reason(
    row: dict[str, object], dep_status: dict[str, str]
) -> BlockedReason:
    """Why this row is not moving — SQL-visible facts only, never a write.

    A row the loop's own predicate calls eligible is `none`, whatever else is
    true of it. The remaining branches EXPLAIN a `0`, and are ordered so the
    most specific cause wins: supersession is a decision about the row,
    a terminal parent is a decision about its lineage, and a future
    `next_attempt_at` is ordinary backoff.

    `waiting_on_dependency` is READ from `depends_on` and never resolved here.
    Resolving it is what `_deps_satisfied` does, and that method DEAD-LETTERS a
    row whose dependency failed — a write, unacceptable on a GET served to a
    READ-only principal.
    """
    if str(row["status"]) != "pending":
        return "none"
    deps = [d.strip() for d in str(row.get("depends_on") or "").split(",") if d.strip()]
    if _as_int(row["sql_eligible"]) == 1:
        # The SQL says yes; the loop asks one more question and so must this.
        if deps and not all(dep_status.get(d) == "completed" for d in deps):
            return "waiting_on_dependency"
        return "none"
    if _as_int(row["superseded"]) != 0:
        return "superseded"
    if str(row["parent_status"] or "") in ("completed", "dead_letter"):
        return "terminal_parent"
    if deps:
        return "waiting_on_dependency"
    return "backoff"
