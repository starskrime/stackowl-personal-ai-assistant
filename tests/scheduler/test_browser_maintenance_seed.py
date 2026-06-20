"""WS-G — the PRODUCER path for the local browser-maintenance jobs.

Three browser-maintenance handlers (``profile_backup``, ``browser_cache_eviction``,
``browser_recycle``) are fully built and registered ONLY when a browser runtime is
available (see ``startup/orchestrator.py``) — but nothing ever seeded their
``jobs`` rows, so the poll loop never dispatched them (WS-E flagged all three as
DANGLING). These are LOCAL maintenance jobs: no delivery target, run on a fixed
cadence with empty params.

``seed_browser_maintenance_schedules(db)`` is the single idempotent producer that
seeds exactly these three rows. It is CO-LOCATED with registration (called from
the browser-available block) so a browser-less box neither registers NOR seeds
them — never a seeded-but-unregistered row that errors every poll.

The two param-REQUIRED handlers (``screenshot_archive``, ``credential_rotation``)
are deliberately NOT seeded here: they require per-job ``params`` (a URL list / a
profile+check_url) and a blank-param row would fail every single poll. They are
``on_demand`` (enqueued per user-configured target), which these tests confirm.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.assembly import seed_browser_maintenance_schedules

pytestmark = pytest.mark.asyncio

# The three local maintenance handlers WS-G seeds, with their expected cadence.
_EXPECTED: dict[str, str] = {
    "profile_backup": "daily@01:00",
    "browser_cache_eviction": "daily@04:30",
    "browser_recycle": "daily@03:00",
}


async def test_seeds_all_three_maintenance_rows(tmp_db: DbPool) -> None:
    """Each local maintenance handler gets exactly one row with the right schedule."""
    await seed_browser_maintenance_schedules(tmp_db)

    for handler_name, schedule in _EXPECTED.items():
        rows = await tmp_db.fetch_all(
            "SELECT schedule, status FROM jobs WHERE handler_name = ?",
            (handler_name,),
        )
        assert len(rows) == 1, f"exactly one {handler_name} row must be seeded"
        assert rows[0]["schedule"] == schedule
        assert rows[0]["status"] == "pending"


async def test_seeded_rows_have_no_delivery_target(tmp_db: DbPool) -> None:
    """LOCAL maintenance jobs carry NO target_channels / target_addresses."""
    await seed_browser_maintenance_schedules(tmp_db)

    for handler_name in _EXPECTED:
        rows = await tmp_db.fetch_all(
            "SELECT target_channels, target_addresses FROM jobs WHERE handler_name = ?",
            (handler_name,),
        )
        assert len(rows) == 1
        # No durable recipient stamped — these never deliver to a chat.
        assert not rows[0]["target_channels"], f"{handler_name} must have no target_channels"
        assert not rows[0]["target_addresses"], f"{handler_name} must have no target_addresses"


async def test_seed_is_idempotent(tmp_db: DbPool) -> None:
    """Calling twice (boot re-run) leaves exactly one row per handler."""
    await seed_browser_maintenance_schedules(tmp_db)
    await seed_browser_maintenance_schedules(tmp_db)

    for handler_name in _EXPECTED:
        rows = await tmp_db.fetch_all(
            "SELECT job_id FROM jobs WHERE handler_name = ?", (handler_name,),
        )
        assert len(rows) == 1, f"re-seed must not duplicate {handler_name}"


async def test_daily_first_run_hour_matches_schedule(tmp_db: DbPool) -> None:
    """next_run_at maps back to the scheduled local hour — never a diverging hour."""
    await seed_browser_maintenance_schedules(tmp_db)

    rows = await tmp_db.fetch_all(
        "SELECT next_run_at FROM jobs WHERE handler_name = ?", ("profile_backup",),
    )
    assert len(rows) == 1
    next_run = datetime.fromisoformat(rows[0]["next_run_at"]).astimezone()
    assert next_run.hour == 1  # daily@01:00


async def test_param_required_handlers_are_not_seeded(tmp_db: DbPool) -> None:
    """screenshot_archive / credential_rotation need per-job params — never blanket-seeded."""
    await seed_browser_maintenance_schedules(tmp_db)

    for handler_name in ("screenshot_archive", "credential_rotation"):
        rows = await tmp_db.fetch_all(
            "SELECT job_id FROM jobs WHERE handler_name = ?", (handler_name,),
        )
        assert rows == [], f"{handler_name} must NOT be seeded (it is on_demand)"


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_param_required_handlers_declare_on_demand() -> None:
    """The two param-required handlers declare trigger_kind='on_demand' so the
    WS-E wiring audit does not flag them as DANGLING when registered.

    Sync test under a module-level asyncio mark — the filterwarnings silences the
    spurious "marked asyncio but not async" notice for this one case.
    """
    from stackowl.scheduler.handlers.credential_rotation import CredentialRotationHandler
    from stackowl.scheduler.handlers.screenshot_archive import ScreenshotArchiveHandler

    sa = ScreenshotArchiveHandler.__new__(ScreenshotArchiveHandler)
    cr = CredentialRotationHandler.__new__(CredentialRotationHandler)
    assert sa.trigger_kind == "on_demand"
    assert cr.trigger_kind == "on_demand"
