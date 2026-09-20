"""Story 4.6 -- ``authz.standing_authority``'s I/O matrix: ``grant``/
``revoke``/``find_active``, and the ``audit_log`` + journal-event writes
every ``grant``/``revoke`` call makes (NFR30/NFR31).

Modelled on ``tests/scheduler/test_pause_resume_are_commands.py``'s own
``tmp_db``-fixture style — a real migrated sqlite db, no mocking.
"""

from __future__ import annotations

import pytest

from stackowl.authz.standing_authority import _target, find_active, grant, revoke
from stackowl.db.pool import DbPool

pytestmark = pytest.mark.asyncio


async def test_grant_writes_an_active_row(tmp_db: DbPool) -> None:
    record = await grant(
        tmp_db, scope_kind="job", scope_id="job-1",
        command_type="delivery.send_message", granted_by="owner",
    )

    assert record.scope_kind == "job"
    assert record.scope_id == "job-1"
    assert record.command_type == "delivery.send_message"
    assert record.granted_by == "owner"
    assert record.provenance == "granted"
    assert record.revoked_at is None

    rows = await tmp_db.fetch_all(
        "SELECT * FROM standing_authority WHERE id = ?", (record.id,),
    )
    assert len(rows) == 1
    assert rows[0]["revoked_at"] is None


async def test_grant_records_an_audit_log_row(tmp_db: DbPool) -> None:
    record = await grant(
        tmp_db, scope_kind="job", scope_id="job-audit",
        command_type="delivery.send_message", granted_by="owner",
    )

    rows = await tmp_db.fetch_all(
        "SELECT actor, target FROM audit_log WHERE event_type = 'authority_granted' "
        "AND target = ?",
        (_target("job", "job-audit", "delivery.send_message"),),
    )
    assert len(rows) == 1
    assert rows[0]["actor"] == "owner"
    # The audit row chains -- a real v2 integrity_hash, never the void ''.
    hash_rows = await tmp_db.fetch_all(
        "SELECT integrity_hash FROM audit_log WHERE event_type = 'authority_granted'",
    )
    assert hash_rows[0]["integrity_hash"] != ""
    assert record.id  # sanity: the record itself is well-formed


async def test_grant_records_an_authority_granted_journal_event(tmp_db: DbPool) -> None:
    record = await grant(
        tmp_db, scope_kind="job", scope_id="job-2",
        command_type="delivery.send_message", granted_by="owl",
    )

    rows = await tmp_db.fetch_all(
        "SELECT actor_id, target_id FROM journal_events WHERE type = 'authority.granted' "
        "AND target_id = ?",
        (_target("job", "job-2", "delivery.send_message"),),
    )
    assert len(rows) == 1
    assert rows[0]["actor_id"] == "owl"
    assert record.granted_by == "owl"


async def test_find_active_returns_the_grant(tmp_db: DbPool) -> None:
    await grant(
        tmp_db, scope_kind="job", scope_id="job-3",
        command_type="delivery.send_message", granted_by="owner",
    )

    found = await find_active(
        tmp_db, scope_kind="job", scope_id="job-3", command_type="delivery.send_message",
    )

    assert found is not None
    assert found.scope_id == "job-3"
    assert found.revoked_at is None


async def test_find_active_with_no_matching_row_returns_none(tmp_db: DbPool) -> None:
    found = await find_active(
        tmp_db, scope_kind="job", scope_id="nonexistent", command_type="delivery.send_message",
    )

    assert found is None


async def test_grant_then_revoke_closes_the_grant(tmp_db: DbPool) -> None:
    await grant(
        tmp_db, scope_kind="job", scope_id="job-4",
        command_type="delivery.send_message", granted_by="owner",
    )

    revoked = await revoke(
        tmp_db, scope_kind="job", scope_id="job-4",
        command_type="delivery.send_message", revoked_by="owner",
    )

    assert revoked is not None
    assert revoked.revoked_at is not None

    found = await find_active(
        tmp_db, scope_kind="job", scope_id="job-4", command_type="delivery.send_message",
    )
    assert found is None


async def test_revoke_records_an_audit_log_row_and_journal_event(tmp_db: DbPool) -> None:
    await grant(
        tmp_db, scope_kind="job", scope_id="job-5",
        command_type="delivery.send_message", granted_by="owner",
    )

    await revoke(
        tmp_db, scope_kind="job", scope_id="job-5",
        command_type="delivery.send_message", revoked_by="owner",
    )

    audit_rows = await tmp_db.fetch_all(
        "SELECT actor FROM audit_log WHERE event_type = 'authority_revoked' AND target = ?",
        (_target("job", "job-5", "delivery.send_message"),),
    )
    assert len(audit_rows) == 1
    assert audit_rows[0]["actor"] == "owner"

    journal_rows = await tmp_db.fetch_all(
        "SELECT actor_id FROM journal_events WHERE type = 'authority.revoked' AND target_id = ?",
        (_target("job", "job-5", "delivery.send_message"),),
    )
    assert len(journal_rows) == 1
    assert journal_rows[0]["actor_id"] == "owner"


async def test_revoke_with_no_active_grant_is_a_no_op(tmp_db: DbPool) -> None:
    result = await revoke(
        tmp_db, scope_kind="job", scope_id="job-never-granted",
        command_type="delivery.send_message", revoked_by="owner",
    )

    assert result is None
    rows = await tmp_db.fetch_all(
        "SELECT * FROM audit_log WHERE event_type = 'authority_revoked'",
    )
    assert rows == []


async def test_a_second_grant_after_revoke_is_active_again(tmp_db: DbPool) -> None:
    """Revoking then granting again — FR32's steady-state lifecycle — leaves
    exactly one ACTIVE row for `find_active` to return, the newest one."""
    await grant(
        tmp_db, scope_kind="job", scope_id="job-6",
        command_type="delivery.send_message", granted_by="owner",
    )
    await revoke(
        tmp_db, scope_kind="job", scope_id="job-6",
        command_type="delivery.send_message", revoked_by="owner",
    )
    second = await grant(
        tmp_db, scope_kind="job", scope_id="job-6",
        command_type="delivery.send_message", granted_by="owner",
    )

    found = await find_active(
        tmp_db, scope_kind="job", scope_id="job-6", command_type="delivery.send_message",
    )

    assert found is not None
    assert found.id == second.id


async def test_revoke_closes_every_active_row_not_just_the_newest(tmp_db: DbPool) -> None:
    """`grant` never dedupes (its own docstring) -- two grant() calls for the
    SAME (scope_kind, scope_id, command_type) with no revoke between them
    leave two active rows. `revoke()` must close ALL of them, or
    `find_active` would keep reporting authority as granted after a
    "successful" revoke."""
    await grant(
        tmp_db, scope_kind="job", scope_id="job-7",
        command_type="delivery.send_message", granted_by="owner",
    )
    await grant(
        tmp_db, scope_kind="job", scope_id="job-7",
        command_type="delivery.send_message", granted_by="owner",
    )
    active_before = await tmp_db.fetch_all(
        "SELECT COUNT(*) AS n FROM standing_authority WHERE scope_id = 'job-7' "
        "AND revoked_at IS NULL",
    )
    assert active_before[0]["n"] == 2  # sanity: the undeduplicated state exists

    revoked = await revoke(
        tmp_db, scope_kind="job", scope_id="job-7",
        command_type="delivery.send_message", revoked_by="owner",
    )

    assert revoked is not None
    found = await find_active(
        tmp_db, scope_kind="job", scope_id="job-7", command_type="delivery.send_message",
    )
    assert found is None
    active_after = await tmp_db.fetch_all(
        "SELECT COUNT(*) AS n FROM standing_authority WHERE scope_id = 'job-7' "
        "AND revoked_at IS NULL",
    )
    assert active_after[0]["n"] == 0
