"""STEER-4 (F111) — digest delivery is exactly-once via a notification_id guard.

F111: the digest is at-least-once — if an adapter's send reaches the user but the
row's post-send delete/response path errors, the row is retained and the SAME
batched notification is re-sent on the next tick (a duplicate user-visible send).

The fix: a notification_id idempotency guard. A ``delivered`` row in
``notification_log`` keyed by ``notification_id`` means the user already received
this message; a subsequent digest tick that re-selects the queue row MUST NOT
re-transport — it just reconciles (deletes the leftover queue row) without a
second send. F110 (recover/poll double-dispatch) is covered by the scheduler CAS
claim and is asserted separately in the scheduler tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.notifications.digest_job import NotificationDigestJob
from stackowl.scheduler.job import Job

pytestmark = pytest.mark.asyncio


class _CountingDeliverer:
    """Records every transport call so a duplicate send is visible."""

    def __init__(self) -> None:
        self.sends: list[tuple[str, str]] = []

    async def transport(self, channel: str, message: str) -> str:
        self.sends.append((channel, message))
        return "delivered"


def _job() -> Job:
    return Job(
        job_id="digest-1",
        handler_name="notification_digest",
        schedule="every 1m",
        idempotency_key="k",
        last_run_at=None,
        next_run_at="2026-01-01T00:00:00Z",
        status="pending",
    )


async def _insert_queue_row(db: DbPool, notification_id: str, message: str) -> None:
    due = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    await db.execute(
        "INSERT INTO notification_queue "
        "(notification_id, message_hash, urgency, category, channel, job_id, "
        "scheduled_for, message) VALUES (?,?,?,?,?,?,?,?)",
        (notification_id, "hash16", "normal", "digest", "cli", None, due, message),
    )


async def test_already_delivered_notification_is_not_resent(tmp_db: DbPool) -> None:
    """A leftover queue row whose notification_id is already delivered → no re-send."""
    TestModeGuard.deactivate()
    deliverer = _CountingDeliverer()
    handler = NotificationDigestJob(tmp_db, deliverer)  # type: ignore[arg-type]

    nid = "note-dup-1"
    await _insert_queue_row(tmp_db, nid, "your digest")
    # Simulate the F111 window: a prior tick DELIVERED (log row written) but the
    # queue-row delete failed (or the response path errored), leaving the row.
    await tmp_db.execute(
        "INSERT INTO notification_log "
        "(notification_id, urgency, category, channel, job_id, delivery_status, "
        "created_at, delivered_at, message_hash) VALUES (?,?,?,?,?,?,?,?,?)",
        (nid, "normal", "digest", "cli", None, "delivered",
         datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(), "hash16"),
    )

    result = handler and await handler.execute(_job())
    assert result.success is True
    # The message was NOT re-sent — the notification_id guard suppressed the duplicate.
    assert deliverer.sends == []
    # The leftover queue row was reconciled away (deleted), so it can't loop.
    rows = await tmp_db.fetch_all(
        "SELECT notification_id FROM notification_queue WHERE notification_id = ?", (nid,)
    )
    assert rows == []


async def test_fresh_notification_is_delivered_once(tmp_db: DbPool) -> None:
    """A row with no prior delivered log is transported exactly once."""
    TestModeGuard.deactivate()
    deliverer = _CountingDeliverer()
    handler = NotificationDigestJob(tmp_db, deliverer)  # type: ignore[arg-type]

    nid = "note-fresh-1"
    await _insert_queue_row(tmp_db, nid, "hello once")
    await handler.execute(_job())
    assert deliverer.sends == [("cli", "hello once")]

    # A second tick: the row is gone (deleted on success) AND a delivered log
    # exists — so even a re-inserted duplicate id would be suppressed. Re-insert
    # the SAME id to prove the guard, then tick again.
    await _insert_queue_row(tmp_db, nid, "hello once")
    await handler.execute(_job())
    assert deliverer.sends == [("cli", "hello once")]  # still ONE send total
