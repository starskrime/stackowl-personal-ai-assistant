"""A sweep that declares work abandoned recorded only how many.

MEASURED 2026-09-10: eight sweeps reclaimed **15** tasks in one night and named
NONE of them. `[loop] reclaimed tasks whose worker never came back` carried
`{"reclaimed": N}` and nothing else.

WHAT THAT COST, and it is why this is a defect rather than a nicety. A loop spent
an hour trying to answer whether any of those workers had still been ALIVE — which
would mean the platform ran the same task twice, with side effects like
`send_message` and `send_file` on that path — and **could not answer it from the
full log corpus AND the database.** The sweep's own UPDATE nulls `lease_owner` and
`lease_expires_at`, so the two facts a reader needs are destroyed by the act being
recorded. A correctness question the evidence cannot settle is the worst state to
leave one in.

THE MESSAGE ALSO ASSERTED A CAUSE IT NEVER CHECKED. "the worker never came back"
is a claim about a worker the sweep does not contact; the row it writes says
"(crash or hang)", which admits it cannot tell those apart. That is this session's
recurring shape a sixth time — `web_fetch` naming a healthy browser (DEBT-277),
consent naming an expiry for a granted approval (DEBT-278), the verifier with one
verdict for two facts (DEBT-279).

`overrun_s` IS THE DISCRIMINATOR. A lease expired hours ago is a worker that is
genuinely gone; one expired by seconds is a worker that may still be running, and
reclaiming it hands the same task to a second one. The sweep still cannot tell —
it never contacts the worker, and that is a deliberate property of a lease — but
the NUMBER lets a reader tell afterwards, which is the whole difference between an
answerable question and an unanswerable one.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests._schema_template import seed_schema

from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "reclaim.db"
    seed_schema(db_path)
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


class _Capture(logging.Handler):
    """Read the named logger directly — `configure_logging` sets propagate=False."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture()
def reclaim_log():
    handler = _Capture()
    logger = logging.getLogger("stackowl.tasks")
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(previous)


async def _seed_expired(
    store: DurableTaskStore, task_id: str, *, worker: str, expired_by: timedelta
) -> None:
    now = datetime.now(tz=UTC)
    await store.enqueue(DurableTask(
        task_id=task_id, owner_id=DEFAULT_PRINCIPAL_ID, goal="g", status="pending",
        max_attempts=30, created_at=now, updated_at=now,
    ))
    await store._db.execute(  # noqa: SLF001
        "UPDATE tasks SET status='running', lease_owner=?, lease_expires_at=? "
        "WHERE task_id=?",
        (worker, (now - expired_by).isoformat(), task_id),
    )


def _detail(handler: _Capture) -> list[dict]:
    rec = next(r for r in handler.records if "reclaim" in r.getMessage().lower())
    return rec._fields["tasks"]  # noqa: SLF001


@pytest.mark.tripwire
@pytest.mark.asyncio
async def test_the_sweep_names_every_task_it_took(pool: DbPool, reclaim_log) -> None:
    """THE 15 UNNAMED. A count cannot be acted on."""
    store = DurableTaskStore(pool)
    await _seed_expired(store, "t-a", worker="w1", expired_by=timedelta(hours=2))
    await _seed_expired(store, "t-b", worker="w2", expired_by=timedelta(seconds=3))

    assert await store.reclaim_expired() == 2
    named = {d["task_id"] for d in _detail(reclaim_log)}
    assert named == {"t-a", "t-b"}, named


@pytest.mark.tripwire
@pytest.mark.asyncio
async def test_it_records_HOW_LATE_each_lease_was(pool: DbPool, reclaim_log) -> None:
    """THE DISCRIMINATOR — hours-late is a dead worker, seconds-late may be a live
    one, and only this number separates them after the fact."""
    store = DurableTaskStore(pool)
    await _seed_expired(store, "t-old", worker="w1", expired_by=timedelta(hours=2))
    await _seed_expired(store, "t-fresh", worker="w2", expired_by=timedelta(seconds=3))

    await store.reclaim_expired()
    by_id = {d["task_id"]: d for d in _detail(reclaim_log)}
    assert by_id["t-old"]["overrun_s"] > 7000, by_id["t-old"]
    assert by_id["t-fresh"]["overrun_s"] < 60, by_id["t-fresh"]


@pytest.mark.tripwire
@pytest.mark.asyncio
async def test_it_names_the_worker_it_took_the_task_FROM(pool: DbPool, reclaim_log) -> None:
    """The UPDATE nulls `lease_owner`, so this is unrecoverable afterwards — which
    is exactly why it must be captured before the write, not read after it."""
    store = DurableTaskStore(pool)
    await _seed_expired(store, "t-a", worker="worker-7", expired_by=timedelta(hours=1))

    await store.reclaim_expired()
    assert _detail(reclaim_log)[0]["worker"] == "worker-7"

    rows = await pool.fetch_all("SELECT lease_owner FROM tasks WHERE task_id='t-a'", ())
    assert rows[0]["lease_owner"] is None, (
        "the owner is gone from the row — the log is the only place it survives"
    )


@pytest.mark.tripwire
@pytest.mark.asyncio
async def test_a_lease_that_has_NOT_expired_is_left_alone(pool: DbPool, reclaim_log) -> None:
    """THE CONTROL. Naming what it took is worthless if it takes the wrong rows —
    and a live worker's task must never be reclaimed while its lease holds."""
    store = DurableTaskStore(pool)
    await _seed_expired(store, "t-live", worker="w1", expired_by=timedelta(seconds=-600))

    assert await store.reclaim_expired() == 0
    assert not [r for r in reclaim_log.records if "reclaim" in r.getMessage().lower()]
    rows = await pool.fetch_all("SELECT status FROM tasks WHERE task_id='t-live'", ())
    assert rows[0]["status"] == "running"


@pytest.mark.tripwire
@pytest.mark.asyncio
async def test_nothing_reclaimed_says_nothing(pool: DbPool, reclaim_log) -> None:
    """A sweep that finds nothing must stay silent — a warning on every quiet pass
    is how a channel stops meaning anything, which this session measured at 24%."""
    store = DurableTaskStore(pool)
    assert await store.reclaim_expired() == 0
    assert not reclaim_log.records
