"""seed_dream_worker_schedule — config-driven cadence + repair of legacy rows.

Verifies:
- A fresh seed with interval_minutes=30 inserts a 'every 30m' job whose
  next_run_at is ~now+30m.
- Re-seeding with a different configured interval REPAIRS the existing row
  (migrates a legacy 'daily@03:00' row to 'every 30m') rather than no-op'ing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.handlers.dream_worker import seed_dream_worker_schedule

pytestmark = pytest.mark.asyncio


async def test_seed_inserts_every_30m(tmp_db: DbPool) -> None:
    await seed_dream_worker_schedule(tmp_db, interval_minutes=30)

    rows = await tmp_db.fetch_all(
        "SELECT schedule, next_run_at FROM jobs WHERE handler_name = 'dream_worker'"
    )
    assert len(rows) == 1
    assert rows[0]["schedule"] == "every 30m"

    next_run = datetime.fromisoformat(rows[0]["next_run_at"])
    expected = datetime.now(UTC) + timedelta(minutes=30)
    # Within a couple of minutes of now+30m.
    assert abs((next_run - expected).total_seconds()) < 120


async def test_seed_is_idempotent_when_schedule_matches(tmp_db: DbPool) -> None:
    await seed_dream_worker_schedule(tmp_db, interval_minutes=30)
    await seed_dream_worker_schedule(tmp_db, interval_minutes=30)

    rows = await tmp_db.fetch_all(
        "SELECT job_id FROM jobs WHERE handler_name = 'dream_worker'"
    )
    assert len(rows) == 1, "second seed with same interval must not duplicate"


async def test_seed_repairs_legacy_schedule(tmp_db: DbPool) -> None:
    """A pre-existing daily@03:00 row must migrate to the configured cadence."""
    # Simulate the live legacy row.
    await tmp_db.execute(
        """INSERT INTO jobs
               (job_id, handler_name, schedule, idempotency_key, last_run_at,
                next_run_at, status, retry_count, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "dream-legacy",
            "dream_worker",
            "daily@03:00",
            "dream_worker",
            None,
            datetime.now(UTC).isoformat(),
            "pending",
            0,
            datetime.now(UTC).isoformat(),
        ),
    )

    await seed_dream_worker_schedule(tmp_db, interval_minutes=30)

    rows = await tmp_db.fetch_all(
        "SELECT job_id, schedule FROM jobs WHERE handler_name = 'dream_worker'"
    )
    assert len(rows) == 1, "repair must not create a second row"
    assert rows[0]["schedule"] == "every 30m"
    assert rows[0]["job_id"] == "dream-legacy", "repair updates the existing row"
