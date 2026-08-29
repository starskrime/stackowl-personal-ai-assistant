"""A task reclaimed over and over must still run out of attempts.

MEASURED 2026-08-28 while confirming the crash-safety net works. TWO RECLAIM
PATHS ENFORCE ONE RULE AND ONLY ONE OF THEM COUNTS THE ATTEMPT.

``DurableTaskStore.reclaim_expired`` returns a stale LEASED row to pending and
deliberately does ``attempt_count = COALESCE(attempt_count,0)+1``. Its docstring
says why: "a task that reliably kills its worker must still reach the ceiling
rather than cycle for ever."

The OTHER path — ``task_liveness_sweep`` -> ``DurableTaskRecoverer.reclaim_one``
-> ``DurableTaskStore.claim_for_recovery`` — writes only ``status`` and
``updated_at``.

AND THE CEILING WAS ENFORCED ONLY ON THE PATH THAT NEVER RUNS.
``last_failure_class='lease_expired'`` was 0 rows ALL TIME — reclaim_expired has
never once fired — while task_liveness_sweep reclaimed 6 tasks on 2026-08-27 and
4 more at 00:33:36 on 08-28. Every reclaim this platform has actually performed
went through the path that does not count. Two live jobmarket rows were observed
reclaimed and restarted with ``attempt_count`` still 0.

So a task that reliably hangs its worker is swept, restarted, and swept again
for ever: it never approaches ``max_attempts`` (30), never reaches
``dead_letter``, and leaves NO trace on the row, so the cycling is invisible to
anyone reading the table too.

WHY THE FLAG RATHER THAN AN UNCONDITIONAL INCREMENT. Boot recovery drives the
SAME CAS. A clean restart of a healthy long-running task is not a failed attempt,
and charging it would walk perfectly good work toward dead_letter every time the
process restarts — and this box exec-replaces the core on every src/ change. The
caller states whether this reclaim is evidence of a hang; the SQL stays in one
place.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore

DEFAULT_PRINCIPAL_ID = "principal-default"


async def _running_task(store: DurableTaskStore, task_id: str) -> None:
    from stackowl.pipeline.durable.task import DurableTask

    await store.enqueue(DurableTask(
        task_id=task_id, owner_id=DEFAULT_PRINCIPAL_ID,
        goal="a task that hangs", status="pending",
    ))
    await store.update_status(task_id, "running")


@pytest.mark.asyncio
async def test_a_sweep_reclaim_counts_the_attempt(tmp_db: DbPool) -> None:
    """THE regression. Without this the retry ceiling is unreachable."""
    store = DurableTaskStore(tmp_db, DEFAULT_PRINCIPAL_ID)
    await _running_task(store, "task-hangs-1")

    assert await store.claim_for_recovery("task-hangs-1", count_attempt=True)

    task = await store.get("task-hangs-1")
    assert task.attempt_count == 1, (
        "a reclaim caused by a hang was not counted, so this task can cycle "
        "for ever without ever reaching max_attempts"
    )


@pytest.mark.asyncio
async def test_boot_recovery_does_NOT_count_the_attempt(tmp_db: DbPool) -> None:
    """The control, and the reason this is a flag and not an unconditional bump.

    Boot recovery drives the same CAS. This box exec-replaces the core on every
    src/ change, so charging an attempt here would march healthy long-running
    work toward dead_letter on a purely operational restart.
    """
    store = DurableTaskStore(tmp_db, DEFAULT_PRINCIPAL_ID)
    await _running_task(store, "task-restart-1")

    assert await store.claim_for_recovery("task-restart-1")

    task = await store.get("task-restart-1")
    assert task.attempt_count == 0


@pytest.mark.asyncio
async def test_repeated_hangs_actually_reach_the_ceiling(tmp_db: DbPool) -> None:
    """The property the counter exists for, stated end to end.

    A counter that increments but never converges would satisfy the first test
    and still cycle for ever in production.
    """
    store = DurableTaskStore(tmp_db, DEFAULT_PRINCIPAL_ID)
    await _running_task(store, "task-hangs-2")

    for _ in range(5):
        await store.claim_for_recovery("task-hangs-2", count_attempt=True)
        await store.update_status("task-hangs-2", "running")

    task = await store.get("task-hangs-2")
    assert task.attempt_count == 5, (
        f"attempts did not accumulate across reclaims: {task.attempt_count}"
    )


@pytest.mark.asyncio
async def test_the_reclaim_says_WHY_on_the_row(tmp_db: DbPool) -> None:
    """A sweep reclaim left no trace at all, so the cycling was unreadable.

    Both jobmarket rows observed reclaimed at 00:33:36 still read
    last_failure_class=NULL afterwards. Nobody looking at the table could tell a
    restarted task from one that had never run.
    """
    store = DurableTaskStore(tmp_db, DEFAULT_PRINCIPAL_ID)
    await _running_task(store, "task-hangs-3")

    await store.claim_for_recovery("task-hangs-3", count_attempt=True)

    task = await store.get("task-hangs-3")
    assert task.last_failure_class, "the row does not record that it was reclaimed"


def test_the_LIVE_sweep_actually_passes_the_flag() -> None:
    """The wiring property, without which the fix is a write with no reader.

    A ``count_attempt`` parameter that no production caller ever passes would
    make every test above pass while production kept cycling exactly as before —
    the same shape as the SSRF guard that was attached but uncallable, and as the
    increment on reclaim_expired that has never once executed.

    Both of the sweep's call sites must pass it: ``execute`` (the periodic scan)
    and ``ensure_available`` (the on-demand reclaim). Wiring only the first would
    leave the second silently uncounted.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    src = (root / "src" / "stackowl" / "scheduler" / "handlers"
           / "task_liveness_sweep.py").read_text(encoding="utf-8")

    total = src.count("reclaim_one(")
    counted = src.count("reclaim_one(task, count_attempt=True)")

    assert total >= 2, f"expected both sweep call sites, found {total}"
    assert counted == total, (
        f"{total - counted} of {total} sweep reclaim call sites do not count the "
        "attempt — a task reclaimed there still cycles for ever"
    )


def test_boot_recovery_still_does_NOT_pass_it() -> None:
    """The other half of the wiring, and the reason it is a flag at all.

    If boot recovery ever starts counting, every core exec-replace charges an
    attempt against healthy long-running work — and this box restarts the core on
    every src/ change.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    src = (root / "src" / "stackowl" / "pipeline" / "durable"
           / "recovery.py").read_text(encoding="utf-8")

    assert "reclaim_one(task, count_attempt=True)" not in src, (
        "boot recovery is charging an attempt for an operational restart"
    )


@pytest.mark.asyncio
async def test_a_queued_retry_is_repointed_at_the_newer_ask(tmp_db: DbPool) -> None:
    """Incident 2026-07-21, preserved through the collapse of retry_queue.

    A second floor while a retry is already queued must not be dropped. This
    exercises the REAL store method — the seam's own tests use a fake store and
    would pass even if repoint_retry did not exist, which is exactly how it was
    nearly shipped missing.
    """
    from stackowl.pipeline.durable.task import DurableTask

    store = DurableTaskStore(tmp_db, DEFAULT_PRINCIPAL_ID)
    await store.enqueue(DurableTask(
        task_id="retry-one", owner_id=DEFAULT_PRINCIPAL_ID, goal="the older ask",
        status="pending", idempotency_key="retry:sess-1",
    ))

    moved = await store.repoint_retry(
        idempotency_key="retry:sess-1", goal="the NEWER ask", trace_id="t2",
    )

    assert moved is True
    assert (await store.get("retry-one")).goal == "the NEWER ask"


@pytest.mark.asyncio
async def test_a_RUNNING_retry_is_left_alone(tmp_db: DbPool) -> None:
    """The control. Rewriting a drive's goal mid-flight makes its answer describe
    work that was never done."""
    from stackowl.pipeline.durable.task import DurableTask

    store = DurableTaskStore(tmp_db, DEFAULT_PRINCIPAL_ID)
    await store.enqueue(DurableTask(
        task_id="retry-two", owner_id=DEFAULT_PRINCIPAL_ID, goal="mid-flight",
        status="pending", idempotency_key="retry:sess-2",
    ))
    await store.update_status("retry-two", "running")

    moved = await store.repoint_retry(
        idempotency_key="retry:sess-2", goal="too late", trace_id="t3",
    )

    assert moved is False
    assert (await store.get("retry-two")).goal == "mid-flight"
