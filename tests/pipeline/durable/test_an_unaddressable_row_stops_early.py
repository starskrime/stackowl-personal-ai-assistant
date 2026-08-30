"""A row that can never prove delivery stops trying almost immediately.

MEASURED 2026-08-29, and it was climbing while I watched. Two RCA verifier tasks::

    destination        'rca'      (a channel NAME, not an address)
    attempt_count      12 -> 20 within minutes, against max_attempts 30
    last_failure_class NULL
    last_error         "retry did not deliver (actuator reported 'pending')"

Completion requires PROOF the outcome reached its destination. A destination with
no addressee can never produce that proof, so ``update_status`` correctly declines,
the row requeues, and it climbs — a model run per attempt, ~37 remaining across the
two, for an answer nobody is waiting for.

WHY THE EXISTING CEILING DID NOT HELP. ``SMALL_CEILING_CLASSES`` keys on
``failure_class``, and this failure carries NULL — ``classify_failure`` has no class
for a bare RuntimeError out of the runner. So the row got the full 30.

WHY NOT KEY ON THE CHANNEL NAME. That would be vendor-shaped and would need a list
that drifts. The honest, general rule is structural: **more attempts cannot conjure
an address.** A row whose destination is set but carries no addressee is not
"failing", it is unable to succeed, and the only correct number of further attempts
is a small one.

MEASURED SAFETY, not assumed. On the live table: 1,288 tasks with NULL destination
(unaffected — they owe no delivery), 31 addressed (unaffected), 20 bare-`telegram`
which ALL COMPLETED (a ceiling they never reached), and 3 bare-`rca`. There is not a
single `cli` task, so the "cli is legitimately address-less" objection is hypothetical
here — and a healthy cli turn never enters this path anyway, because its delivery is
proven and it never fails this way.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests._schema_template import seed_schema

from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "unaddr.db"
    seed_schema(db_path)
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def _seed(store: DurableTaskStore, task_id: str, destination: str | None) -> None:
    now = datetime.now(tz=UTC)
    await store.enqueue(DurableTask(
        task_id=task_id, owner_id=DEFAULT_PRINCIPAL_ID, goal="g", status="pending",
        destination=destination, channel="rca", max_attempts=30,
        created_at=now, updated_at=now,
    ))


@pytest.mark.asyncio
async def test_an_unaddressable_row_dead_letters_within_a_few_attempts(
    pool: DbPool,
) -> None:
    """The measured case. It must stop long before 30."""
    store = DurableTaskStore(pool)
    await _seed(store, "unaddr", "rca")

    statuses = []
    for _ in range(5):
        statuses.append(await store.fail_and_requeue(
            "unaddr", error="retry did not deliver", failure_class="",
        ))
    assert "dead_letter" in statuses, (
        f"an unaddressable row is still climbing: {statuses} — more attempts cannot "
        "conjure an address, so this burns a model run per attempt for nothing"
    )
    assert statuses.index("dead_letter") <= 3, (
        f"it stopped, but not early enough: {statuses}"
    )


@pytest.mark.asyncio
async def test_an_ADDRESSED_row_keeps_its_full_budget(pool: DbPool) -> None:
    """The guard must be narrow.

    A row that CAN be delivered to deserves its 30 attempts — capping those would
    abandon real work that a later attempt might land.
    """
    store = DurableTaskStore(pool)
    await _seed(store, "addr", "telegram:72055773")

    statuses = [
        await store.fail_and_requeue("addr", error="boom", failure_class="")
        for _ in range(5)
    ]
    assert "dead_letter" not in statuses, (
        f"an addressed row was capped early: {statuses}"
    )


@pytest.mark.asyncio
async def test_a_row_with_NO_destination_keeps_its_full_budget(pool: DbPool) -> None:
    """NULL means 'owes no delivery', which is not the same as 'cannot deliver'.

    1,288 live rows are in this state — a sub-goal whose parent delivers. Capping
    them would gut the loop.
    """
    store = DurableTaskStore(pool)
    await _seed(store, "nodest", None)

    statuses = [
        await store.fail_and_requeue("nodest", error="boom", failure_class="")
        for _ in range(5)
    ]
    assert "dead_letter" not in statuses, (
        f"a sub-goal with no destination was capped early: {statuses}"
    )
