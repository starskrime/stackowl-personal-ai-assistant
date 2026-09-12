"""The card carried 23 static fields and nothing about now. A04.1.

MEASURED 2026-09-11 before building: `OwlAgentManifest` is the ONE agent
descriptor, read by 29 modules in `src/`, and every field is configuration. The
`owls` table adds nothing runtime. Meanwhile `tasks.owl_name` is written on all
849 populated rows and is a query predicate in **ZERO** SQL statements anywhere
in `src/` — written everywhere, read nowhere, on exactly the column this item
needs.

THE PANEL'S DISAGREEMENT IS THE DESIGN, so it is pinned here rather than
averaged away. One lens said build over `tasks` (it alone carries status); one
said a `tasks`-only view reports seven of eleven agents as idle forever. Both are
right about their own store, and the resolution is that they answer DIFFERENT
QUESTIONS:

  * `tasks` answers NOW — it has `status` and `lease_expires_at`.
  * `task_outcomes` answers EVER — it has **no status column at all**; every row
    is terminal, captured at end-of-pipeline. 19,900 rows cannot say what is
    happening now, at any volume.

So the two facts are returned separately named and separately sourced, and
`test_there_is_no_fused_status_field` pins that. A single enum spanning a live
table and a history table would reach `/api/v1` JSON and could never be split
back out.

AND A ZERO IN `in_flight` IS USUALLY CORRECT — this nearly shipped backwards
twice. `enqueue_turn_task` has exactly ONE call site, and the comment above it
says only the gateway owl route reaches `deliver.py`, which COMPLETES the row, so
"enqueuing a command or parliament turn would create a task nothing ever closes".
`rca_gatherer`, `hypothesis` and `verifier` are parliament/RCA stages running
inside another owl's turn: 3,240 outcomes and zero tasks is the truth. A reader
that unioned the stores to make all eleven look busy would have manufactured
coverage — this repo's "message asserting a cause nobody checked" family, shipped
as a product surface.
"""

from __future__ import annotations

import inspect
from dataclasses import fields
from typing import Any

import pytest

from stackowl.owls.activity import ActivityGaps, OwlActivity, read_owl_activity
from stackowl.owls.manifest import OwlAgentManifest

_OWNER = "principal-test"


def _owl(name: str) -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name, role="r", system_prompt="p", model_tier="fast",
    )


class _Db:
    """Records every statement, so the owner predicate cannot be lost quietly.

    A single-principal install makes a scoped and an unscoped SELECT
    indistinguishable in their RESULTS (DEBT-292), so the assertion lives here
    rather than in a tripwire that may not be derived.
    """

    def __init__(self, tasks: list[dict[str, Any]], outcomes: list[dict[str, Any]]):
        self._tasks = tasks
        self._outcomes = outcomes
        self.seen: list[str] = []

    async def fetch_all(self, sql: str, params: Any = ()) -> list[dict[str, Any]]:
        assert "owner_id = ?" in sql, f"a statement lost its owner predicate: {sql}"
        assert params and params[0] == _OWNER, f"wrong principal: {params}"
        self.seen.append(sql)
        return self._tasks if "FROM tasks" in sql else self._outcomes


class TestItAnswersBothQuestionsSeparately:
    @pytest.mark.tripwire
    async def test_now_comes_from_tasks_and_ever_comes_from_outcomes(self) -> None:
        db = _Db(
            tasks=[
                {"owl_name": "secretary", "status": "running", "n": 2},
                {"owl_name": "secretary", "status": "pending", "n": 5},
                {"owl_name": "secretary", "status": "completed", "n": 400},
                {"owl_name": "secretary", "status": "parked", "n": 3},
                {"owl_name": "verifier", "status": "recovering", "n": 1},
            ],
            outcomes=[
                {"owl_name": "secretary", "n": 120, "last_at": 1789164089.0},
                {"owl_name": "hypothesis", "n": 17, "last_at": 1789155289.0},
            ],
        )
        acts, gaps = await read_owl_activity(
            db, [_owl("secretary"), _owl("verifier"), _owl("hypothesis")],
            owner_id=_OWNER, since_epoch=0.0,
        )
        by = {a.manifest.name: a for a in acts}

        assert by["secretary"].in_flight == 2, "running counts as now"
        assert by["secretary"].pending == 5
        assert by["verifier"].in_flight == 1, "recovering counts as now"
        assert by["secretary"].in_flight == 2, (
            "a parked task was counted as in-flight — parked is UNFINISHED but "
            "nothing is working on it, and the live set is taken from the store's "
            "own CAS predicate rather than chosen here"
        )
        assert by["secretary"].recent_turns == 120
        assert by["secretary"].last_seen_at == 1789164089.0
        assert gaps == ActivityGaps(0, 0, 0)

    @pytest.mark.tripwire
    async def test_a_stage_owl_with_NO_task_row_is_not_reported_as_dead(self) -> None:
        """The reading that was wrong twice. `hypothesis` runs inside another
        owl's turn and correctly has zero `tasks` rows; it is not idle, and the
        record that says so is its outcome history — which must NOT be laundered
        into `in_flight`."""
        db = _Db(
            tasks=[],
            outcomes=[{"owl_name": "hypothesis", "n": 3075, "last_at": 1789155289.0}],
        )
        acts, _ = await read_owl_activity(
            db, [_owl("hypothesis")], owner_id=_OWNER, since_epoch=0.0,
        )

        assert acts[0].in_flight == 0, (
            "history was laundered into liveness — a stage owl holds no task row "
            "and `in_flight` must say so honestly"
        )
        assert acts[0].recent_turns == 3075
        assert acts[0].last_seen_at == 1789155289.0

    @pytest.mark.tripwire
    def test_there_is_no_fused_status_field(self) -> None:
        """The irreversible mistake, pinned. One enum over a live table and a
        history table becomes a public `/api/v1` key and can never be split."""
        names = {f.name for f in fields(OwlActivity)}
        assert "status" not in names and "state" not in names, (
            f"a fused status field appeared on the activity record: {sorted(names)}"
        )
        assert {"in_flight", "pending", "last_seen_at", "recent_turns"} <= names


class TestItSaysWhatItCouldNotSee:
    @pytest.mark.tripwire
    async def test_unattributed_and_orphaned_work_is_COUNTED_not_dropped(self) -> None:
        """MEASURED on the live database: 324 task rows name an owl that no
        longer exists and 76 (owner-scoped) carry no `owl_name` at all. An INNER
        JOIN would hide them behind a total that looks plausible."""
        db = _Db(
            tasks=[
                {"owl_name": "secretary", "status": "pending", "n": 5},
                {"owl_name": "", "status": "completed", "n": 76},
                {"owl_name": "Brain", "status": "failed", "n": 110},
                {"owl_name": "sysfup", "status": "failed", "n": 119},
            ],
            outcomes=[{"owl_name": "headhunter", "n": 614, "last_at": 1.0}],
        )
        acts, gaps = await read_owl_activity(
            db, [_owl("secretary")], owner_id=_OWNER, since_epoch=0.0,
        )

        assert len(acts) == 1, "an orphan invented an agent that does not exist"
        assert gaps.unattributed_tasks == 76
        assert gaps.orphaned_tasks == 229
        assert gaps.orphaned_outcomes == 614


class TestItCannotBecomeAnNPlusOne:
    @pytest.mark.tripwire
    async def test_it_issues_exactly_two_grouped_queries(self) -> None:
        """There is no index on `tasks(owl_name)` — the live ones are owner,
        status, parent, session, claimable and lease-expiry — over a table
        holding 16.8 MB of `checkpoint_blob`. A per-owl predicate would be an
        N+1 that looks fine with eleven owls and does not with a thousand."""
        db = _Db(tasks=[], outcomes=[])
        owls = [_owl(f"owl{i}") for i in range(25)]

        await read_owl_activity(db, owls, owner_id=_OWNER, since_epoch=0.0)

        assert len(db.seen) == 2, f"{len(db.seen)} queries for 25 owls: {db.seen}"
        assert sum("GROUP BY" in s for s in db.seen) == 2

    @pytest.mark.tripwire
    async def test_the_outcome_read_is_time_bounded(self) -> None:
        """`task_outcomes` only grows. An unbounded MAX/COUNT over it is the
        no-decay shape, and the index it must use is
        `(owner_id, owl_name, captured_at)` — a query without the third column
        cannot use it."""
        db = _Db(tasks=[], outcomes=[])
        await read_owl_activity(db, [_owl("a")], owner_id=_OWNER, since_epoch=123.0)

        outcome_sql = [s for s in db.seen if "task_outcomes" in s]
        assert len(outcome_sql) == 1
        assert "captured_at >= ?" in outcome_sql[0]


class TestTheCardIsSharedByReference:
    @pytest.mark.tripwire
    async def test_the_manifest_is_the_SAME_object_not_a_copy(self) -> None:
        """29 modules already share this descriptor. A copy here is a second
        descriptor with extra steps — the exact outcome A04.1's original,
        mistaken gap statement would have produced."""
        card = _owl("secretary")
        acts, _ = await read_owl_activity(
            _Db([], []), [card], owner_id=_OWNER, since_epoch=0.0,
        )

        assert acts[0].manifest is card

    @pytest.mark.tripwire
    def test_the_A04_2_hole_is_NAMED_rather_than_filled(self) -> None:
        """An agent wedged mid-turn inside another owl's turn holds no task row
        and has no durable home at all. Filling `live_activation` from outcome
        history would be manufactured coverage; A04.2 is the item that fills it."""
        from stackowl.owls import activity as mod

        assert "live_activation" in {f.name for f in fields(OwlActivity)}
        src = inspect.getsource(mod)
        assert "A04.2" in src, (
            "the unfilled field no longer names the item that fills it, so a "
            "future reader cannot tell a hole from an oversight"
        )
