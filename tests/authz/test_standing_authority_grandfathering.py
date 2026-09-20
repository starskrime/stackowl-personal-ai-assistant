"""``authz/delivery_grandfather.py`` -- Story 4.8's own migration of EXISTING
enabled jobs onto standing authority (deferred from Story 4.6, per epic-4-
context's Cross-Story Dependencies).

Proves: every enabled job whose ``handler_name`` owns a declared delivery
command type gets a ``provenance="grandfathered"`` grant; a disabled job and
``telegram_canary`` (Boundaries: "do not grandfather... a synthetic probe")
get none; the routine is idempotent across a repeat boot; and the seed-time
``provenance="seeded"`` counterpart (``seed_job_delivery_authority``) is
likewise idempotent and correctly provenanced.
"""

from __future__ import annotations

import pytest

from stackowl.authz.delivery_grandfather import (
    HANDLER_DELIVERY_COMMAND_TYPES,
    grandfather_existing_job_delivery_authority,
    seed_job_delivery_authority,
)
from stackowl.authz.standing_authority import find_active
from stackowl.db.pool import DbPool

pytestmark = pytest.mark.asyncio

_INSERT_JOB_SQL = (
    "INSERT INTO jobs (job_id, handler_name, schedule, idempotency_key, "
    "last_run_at, next_run_at, status, enabled, created_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


async def _insert_job(db: DbPool, job_id: str, handler_name: str, *, enabled: bool = True) -> None:
    await db.execute(
        _INSERT_JOB_SQL,
        (job_id, handler_name, "daily@08:00", f"{job_id}:k", None,
         "2026-01-01T00:00:00Z", "pending", int(enabled), "2026-01-01T00:00:00Z"),
    )


async def test_handler_delivery_command_types_covers_all_four_job_handlers() -> None:
    assert HANDLER_DELIVERY_COMMAND_TYPES == {
        "morning_brief": "notifications.deliver_brief",
        "check_in": "notifications.deliver_check_in",
        "goal_execution": "notifications.deliver_goal_result",
        "notification_digest": "notifications.deliver_digest",
    }


async def test_grandfathers_every_enabled_job_with_a_delivery_command_type(
    tmp_db: DbPool,
) -> None:
    await _insert_job(tmp_db, "job-brief-1", "morning_brief")
    await _insert_job(tmp_db, "job-checkin-1", "check_in")
    await _insert_job(tmp_db, "job-goal-1", "goal_execution")
    await _insert_job(tmp_db, "job-digest-1", "notification_digest")

    granted = await grandfather_existing_job_delivery_authority(tmp_db)

    assert granted == 4
    for job_id, command_type in (
        ("job-brief-1", "notifications.deliver_brief"),
        ("job-checkin-1", "notifications.deliver_check_in"),
        ("job-goal-1", "notifications.deliver_goal_result"),
        ("job-digest-1", "notifications.deliver_digest"),
    ):
        record = await find_active(tmp_db, scope_kind="job", scope_id=job_id, command_type=command_type)
        assert record is not None
        assert record.provenance == "grandfathered"
        assert record.granted_by == "autonomous"


async def test_disabled_job_is_not_grandfathered(tmp_db: DbPool) -> None:
    await _insert_job(tmp_db, "job-brief-disabled", "morning_brief", enabled=False)

    granted = await grandfather_existing_job_delivery_authority(tmp_db)

    assert granted == 0
    record = await find_active(
        tmp_db, scope_kind="job", scope_id="job-brief-disabled",
        command_type="notifications.deliver_brief",
    )
    assert record is None


async def test_telegram_canary_is_never_grandfathered(tmp_db: DbPool) -> None:
    """Boundaries: 'Do not grandfather telegram_canary -- a synthetic probe,
    not named in AC1's scope.'"""
    await _insert_job(tmp_db, "job-canary-1", "telegram_canary")

    granted = await grandfather_existing_job_delivery_authority(tmp_db)

    assert granted == 0
    rows = await tmp_db.fetch_all(
        "SELECT 1 FROM standing_authority WHERE scope_id = 'job-canary-1'",
    )
    assert rows == []


async def test_grandfathering_is_idempotent_across_a_repeat_boot(tmp_db: DbPool) -> None:
    await _insert_job(tmp_db, "job-brief-repeat", "morning_brief")

    first = await grandfather_existing_job_delivery_authority(tmp_db)
    second = await grandfather_existing_job_delivery_authority(tmp_db)

    assert first == 1
    assert second == 0  # no duplicate active grant minted
    rows = await tmp_db.fetch_all(
        "SELECT COUNT(*) AS n FROM standing_authority WHERE scope_id = 'job-brief-repeat' "
        "AND revoked_at IS NULL",
    )
    assert rows[0]["n"] == 1


async def test_other_handler_names_are_not_grandfathered(tmp_db: DbPool) -> None:
    """Any handler_name outside the closed mapping (e.g. a maintenance sweep)
    is silently skipped — this routine was never asked to authorize it."""
    await _insert_job(tmp_db, "job-sweep-1", "conversation_sweep")

    granted = await grandfather_existing_job_delivery_authority(tmp_db)

    assert granted == 0


# --------------------------------------------------------------------------- seed_job_delivery_authority


async def test_seed_job_delivery_authority_grants_with_seeded_provenance(tmp_db: DbPool) -> None:
    await seed_job_delivery_authority(
        tmp_db, job_id="morning_brief-abc123", command_type="notifications.deliver_brief",
    )

    record = await find_active(
        tmp_db, scope_kind="job", scope_id="morning_brief-abc123",
        command_type="notifications.deliver_brief",
    )
    assert record is not None
    assert record.provenance == "seeded"


async def test_seed_job_delivery_authority_is_idempotent(tmp_db: DbPool) -> None:
    await seed_job_delivery_authority(
        tmp_db, job_id="morning_brief-repeat", command_type="notifications.deliver_brief",
    )
    await seed_job_delivery_authority(
        tmp_db, job_id="morning_brief-repeat", command_type="notifications.deliver_brief",
    )

    rows = await tmp_db.fetch_all(
        "SELECT COUNT(*) AS n FROM standing_authority WHERE scope_id = 'morning_brief-repeat' "
        "AND revoked_at IS NULL",
    )
    assert rows[0]["n"] == 1
