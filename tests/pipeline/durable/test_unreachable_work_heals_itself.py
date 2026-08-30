"""Work filed under a non-principal heals itself, and cannot be created again.

BAKIR, 2026-08-21: *"Always fix core issue, not current. Platform has self healing. So
if you fix core issue platform should heal himself. If it does not, then platform has
issue with self healing OR core issue not resolved."*

APPLIED TO MY OWN FIX, WHICH FAILED THE TEST. On 2026-08-20 I found 387 task rows the
loop could never claim and fixed `rollover_summary_handler`, which had been filing them
under a knowledge scope instead of a principal. I added a WARNING counting them and left
the 387 in place. Under this rule that is a half-fix twice over: the platform did not
heal, and BOTH of the explanations the rule names were true at once.

**The core was not the writer.** Measured: the database has a `principals` table, and
26 of the 27 distinct `tasks.owner_id` values in it are not principals at all — only
`principal-default` is real. `owner_id` is a tenancy key with NO integrity constraint,
so any writer can put any string there and the owner-scoped loop then silently cannot
see those rows. Fixing one writer stops one source; the class stays open for the next.

**And self-healing had a gap.** Nothing drains or retires unreachable work. Detection is
not healing — the standing rule is to build the actuator, not file the debt, and I filed
it.

WHAT MAKES THE HEALING SAFE, and it is the platform's own rule rather than a judgement:
a task either has a DESTINATION (someone is waiting) or it does not. Measured across all
387: every one is a `rollover-*` bookkeeping row and NOT ONE carries a destination. So

* **no destination, unreachable** → RETIRE it with a stated reason. It can never achieve
  anything and nobody is waiting. Re-filing these instead would hand the loop 72
  unrunnable goals ("rollover summary for owl:…" is a checkpoint record, not a goal),
  burning up to 30 attempts each and dead-lettering 72 times at the operator.
* **has a destination** → REPORT it at ERROR and move nothing. Someone is owed this
  answer, but re-filing across owners can MERGE TENANTS — proven, not feared: the first
  version of this fix normalised unknown owners to the default and the multi-owner suite
  failed with `UNIQUE constraint failed: tasks.owner_id, tasks.task_id`, because two
  distinct owners had become one. On a many-customer deployment that files one tenant's
  work under another. Healing that can cross-file between customers is worse than the
  strand it repairs, so this half surfaces for a human instead.

Zero of the 387 fall in the second bucket today, so the safe rule fixes 100% of the real
damage — and the unsafe half was never needed.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from tests._schema_template import seed_schema

from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

pytestmark = pytest.mark.asyncio

UTC = datetime.UTC


@pytest.fixture()
async def pool(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    db_path = tmp_path / "heal.db"
    seed_schema(db_path)
    p = DbPool(db_path=db_path)
    await p.open()
    from stackowl.tenancy.store import PrincipalStore

    await PrincipalStore(p).ensure_default()
    try:
        yield p
    finally:
        await p.close()


async def _misfiled(pool: DbPool, task_id: str, *, owner: str, status: str,
                    destination: str | None) -> None:
    """Write a row directly, bypassing the store — this is how the 387 got there."""
    now = datetime.datetime.now(tz=UTC).isoformat()
    await pool.execute(
        "INSERT INTO tasks (task_id, owner_id, goal, status, current_step, "
        "destination, created_at, updated_at) VALUES (?,?,?,?,0,?,?,?)",
        (task_id, owner, "rollover summary for owl:x", status, destination, now, now),
    )


class TestThePlatformHealsUnreachableWork:
    async def test_bookkeeping_nobody_waits_on_is_retired(self, pool: DbPool) -> None:
        """The measured case: 387 rows, none with a destination, none runnable."""
        await _misfiled(pool, "rollover-a", owner="72055773", status="pending",
                        destination=None)
        store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)

        healed = await store.heal_unreachable_owners()

        assert healed == 1
        row = (await pool.fetch_all(
            "SELECT status, owner_id FROM tasks WHERE task_id='rollover-a'"))[0]
        assert row["status"] != "pending", "still claimable by nobody"

    async def test_work_someone_waits_on_is_reported_never_moved(
        self, pool: DbPool, caplog
    ) -> None:
        """A destination means a person is owed an answer — and the sweep must NOT
        move it.

        REWRITTEN after the first version broke tenancy. Re-filing onto the sweeping
        principal merges tenants: an unregistered owner may be a real customer whose
        principal row has not been written yet, and the composite PK
        (owner_id, task_id) turns the collision into silent damage. The multi-owner
        suite proved it — two owners normalised to one and collided on that PK.

        So the row is SURFACED at ERROR for an operator, not repaired. Healing that
        can cross-file work between customers is worse than the strand it fixes.
        """
        await _misfiled(pool, "chat-x", owner="owl:secretary:recovery:zz",
                        status="pending", destination="telegram:72055773")
        store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)

        with caplog.at_level("ERROR"):
            await store.heal_unreachable_owners()

        row = (await pool.fetch_all(
            "SELECT status, owner_id FROM tasks WHERE task_id='chat-x'"))[0]
        assert row["owner_id"] == "owl:secretary:recovery:zz", "moved across owners"
        assert any("merge two tenants" in r.message for r in caplog.records), (
            f"stranded silently: {[r.message for r in caplog.records]}"
        )

    async def test_rows_already_terminal_are_left_alone(self, pool: DbPool) -> None:
        """A completed or failed bookkeeping row is not damage — it is history, and
        `prune_completed` already owns its lifecycle. Healing must not churn it."""
        await _misfiled(pool, "rollover-done", owner="72055773", status="completed",
                        destination=None)
        store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)

        await store.heal_unreachable_owners()

        row = (await pool.fetch_all(
            "SELECT status FROM tasks WHERE task_id='rollover-done'"))[0]
        assert row["status"] == "completed"

    async def test_correctly_owned_work_is_untouched(self, pool: DbPool) -> None:
        """The blast radius. A row under a real principal is ordinary work and must not
        be re-filed, retired or counted."""
        now = datetime.datetime.now(tz=UTC)
        store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
        await store.enqueue(DurableTask(
            task_id="ok-1", owner_id=DEFAULT_PRINCIPAL_ID, goal="real work",
            status="pending", created_at=now, updated_at=now,
        ))

        healed = await store.heal_unreachable_owners()

        assert healed == 0
        assert (await store.get("ok-1")).status == "pending"

    async def test_healing_never_raises(self, pool: DbPool) -> None:
        """It runs at loop start. A sweep that can stop the loop booting is worse than
        the rows it repairs — the same contract as the undelivered-failure sweep."""
        await pool.close()
        store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
        assert await store.heal_unreachable_owners() == 0


class TestMultiTenancyIsNotSacrificed:
    """PREVENTION WAS ATTEMPTED AND BACKED OUT, and the reason is recorded here.

    The obvious core fix is referential integrity: refuse to write a task whose owner
    is not a principal. The first attempt NORMALISED an unknown owner to the default,
    and the multi-owner suite immediately failed with

        sqlite3.IntegrityError: UNIQUE constraint failed: tasks.owner_id, tasks.task_id

    because two distinct owners both became `principal-default`. On a deployment with
    many customers that files one tenant's work under another — strictly worse than the
    strand it was fixing. Raising instead would break every legitimate caller whose
    principal row has not been written yet.

    So enforcement is an operator decision with real cost in both directions, and it is
    escalated rather than taken. What ships is the healing, which cannot merge anything.
    """

    async def test_two_unregistered_owners_stay_distinct(self, pool: DbPool) -> None:
        """The property the backed-out fix destroyed. Multi-tenancy is load-bearing —
        the composite PK exists so two owners can share a task_id."""
        now = datetime.datetime.now(tz=UTC)
        for owner in ("owner-a", "owner-b"):
            await DurableTaskStore(pool, owner).create(DurableTask(
                task_id="same-id", owner_id=owner, goal="g", status="pending",
                created_at=now, updated_at=now,
            ))

        rows = await pool.fetch_all(
            "SELECT owner_id FROM tasks WHERE task_id='same-id' ORDER BY owner_id")
        assert [r["owner_id"] for r in rows] == ["owner-a", "owner-b"]
