"""``notifications.deliver_digest`` -- Story 4.8's handler-level I/O matrix,
plus proof that ``NotificationDigestJob._flush_row`` submits through the one
door instead of calling ``ProactiveDeliverer.transport`` directly.

The digest flush is a plain ``transport(channel, message)`` (the routing
decision was already made when the notification was first batched --
``digest_job.py``'s own module docstring), never ``deliver_for_job``, so its
payload/handler shape is simpler than the 3 job-scoped delivery commands.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

import stackowl.notifications.commands  # noqa: F401 -- registration side effect
from stackowl.authz.standing_authority import grant
from stackowl.commands.spec.execute import execute_command_task
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.notifications.commands import DELIVER_DIGEST
from stackowl.notifications.digest_job import NotificationDigestJob
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.scheduler.job import Job
from stackowl.tools.consent import PRINCIPAL_AUTONOMOUS_SCHEDULER

pytestmark = pytest.mark.asyncio


class _FakeDeliverer:
    def __init__(self, status: str = "delivered") -> None:
        self.status = status
        self.calls: list[tuple[str, str]] = []
        self.contexts: list[object] = []

    async def transport(self, channel: str, message: str, *, context: object = None) -> str:
        self.calls.append((channel, message))
        self.contexts.append(context)
        return self.status


def _digest_task(command_id: str, *, channel: str = "cli", message: str = "digest body") -> DurableTask:
    payload = {"job_id": "digest-job-1", "channel": channel, "message": message}
    return DurableTask(
        task_id=f"cmd-{command_id}", goal=f"command:{DELIVER_DIGEST}", status="running",
        kind="command", command_type=DELIVER_DIGEST,
        command_payload=json.dumps(payload),
        command_id=command_id, requester_kind="autonomous", gate_verdict="approved",
    )


# --------------------------------------------------------------------------- handler-level


async def test_deliver_digest_handler_transports(tmp_db: DbPool) -> None:
    deliverer = _FakeDeliverer(status="delivered")
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        outcome = await execute_command_task(_digest_task("cmd-dg-1"))
    finally:
        reset_services(token)

    assert outcome.success is True
    assert outcome.result["delivery_status"] == "delivered"
    assert deliverer.calls == [("cli", "digest body")]
    assert deliverer.contexts[0] is not None


async def test_deliver_digest_handler_transport_failed_is_unsuccessful(tmp_db: DbPool) -> None:
    deliverer = _FakeDeliverer(status="failed")
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        outcome = await execute_command_task(_digest_task("cmd-dg-2"))
    finally:
        reset_services(token)

    assert outcome.success is False
    assert outcome.result["delivery_status"] == "failed"


async def test_deliver_digest_re_run_does_not_retransport(tmp_db: DbPool) -> None:
    deliverer = _FakeDeliverer(status="delivered")
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        first = await execute_command_task(_digest_task("cmd-dg-reclaim"))
        second = await execute_command_task(_digest_task("cmd-dg-reclaim"))
    finally:
        reset_services(token)
    assert first.success is True
    assert second.success is False  # a replay does not reassert a confirmed delivery
    assert second.result.get("already_sent") is True
    assert second.result.get("delivery_status") == "unknown"
    assert len(deliverer.calls) == 1


async def test_deliver_digest_no_db_pool_fails_cleanly() -> None:
    token = set_services(StepServices())
    try:
        outcome = await execute_command_task(_digest_task("cmd-dg-no-db"))
    finally:
        reset_services(token)
    assert outcome.success is False
    assert "database" in (outcome.error or "")


async def test_deliver_digest_no_deliverer_fails_cleanly(tmp_db: DbPool) -> None:
    token = set_services(StepServices(db_pool=tmp_db))
    try:
        outcome = await execute_command_task(_digest_task("cmd-dg-no-deliverer"))
    finally:
        reset_services(token)
    assert outcome.success is False
    assert "deliverer" in (outcome.error or "")


# --------------------------------------------------------------------------- wiring


def _digest_job() -> Job:
    return Job(
        job_id="digest-real-1",
        handler_name="notification_digest",
        schedule="every 5m",
        idempotency_key="k",
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
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


async def test_digest_job_autonomous_with_matching_grant_delivers_without_approval(
    tmp_db: DbPool,
) -> None:
    TestModeGuard.deactivate()
    job = _digest_job()
    await grant(
        tmp_db, scope_kind="job", scope_id=job.job_id,
        command_type=DELIVER_DIGEST, granted_by="autonomous", provenance="seeded",
    )
    deliverer = _FakeDeliverer(status="delivered")
    handler = NotificationDigestJob(tmp_db, deliverer)  # type: ignore[arg-type]
    await _insert_queue_row(tmp_db, "nid-granted", "hello")

    ttoken = TraceContext.start(
        session_key=None, trace_id="t-digest-wire", interactive=False, channel=None,
        principal=PRINCIPAL_AUTONOMOUS_SCHEDULER,
    )
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        result = await handler.execute(job)
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)

    assert result.success is True
    assert deliverer.calls == [("cli", "hello")]


async def test_digest_job_autonomous_with_no_grant_does_not_transport(tmp_db: DbPool) -> None:
    TestModeGuard.deactivate()
    job = _digest_job()
    deliverer = _FakeDeliverer(status="delivered")
    handler = NotificationDigestJob(tmp_db, deliverer)  # type: ignore[arg-type]
    await _insert_queue_row(tmp_db, "nid-ungranted", "hello")

    ttoken = TraceContext.start(
        session_key=None, trace_id="t-digest-wire-2", interactive=False, channel=None,
        principal=PRINCIPAL_AUTONOMOUS_SCHEDULER,
    )
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        await handler.execute(job)
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)

    # No matching authority => the command parks (pending_approval, NOT a
    # transport failure) => the row is RETAINED for the next tick, never lost.
    assert deliverer.calls == []
    rows = await tmp_db.fetch_all(
        "SELECT notification_id, attempts FROM notification_queue WHERE notification_id = ?",
        ("nid-ungranted",),
    )
    assert len(rows) == 1
    assert rows[0]["attempts"] == 0  # NOT counted toward the dead-letter cap


async def test_digest_job_pending_approval_is_never_dead_lettered(tmp_db: DbPool) -> None:
    """Review fix — a PARKED command (no matching standing authority) must
    retry indefinitely, never dead-letter: even a row already one attempt
    below `_MAX_FLUSH_ATTEMPTS` must survive another parked tick untouched,
    unlike a genuine transport failure at the same attempts count."""
    TestModeGuard.deactivate()
    job = _digest_job()
    deliverer = _FakeDeliverer(status="delivered")
    handler = NotificationDigestJob(tmp_db, deliverer)  # type: ignore[arg-type]
    due = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    await tmp_db.execute(
        "INSERT INTO notification_queue "
        "(notification_id, message_hash, urgency, category, channel, job_id, "
        "scheduled_for, message, attempts) VALUES (?,?,?,?,?,?,?,?,?)",
        ("nid-near-cap", "hash16", "normal", "digest", "cli", None, due, "hello", 4),
    )

    ttoken = TraceContext.start(
        session_key=None, trace_id="t-digest-wire-3", interactive=False, channel=None,
        principal=PRINCIPAL_AUTONOMOUS_SCHEDULER,
    )
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        await handler.execute(job)
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)

    assert deliverer.calls == []
    rows = await tmp_db.fetch_all(
        "SELECT notification_id, attempts FROM notification_queue WHERE notification_id = ?",
        ("nid-near-cap",),
    )
    # Retained, untouched — NOT dead-lettered and NOT bumped, despite already
    # being one attempt below the cap.
    assert len(rows) == 1
    assert rows[0]["attempts"] == 4
    outbox_rows = await tmp_db.fetch_all(
        "SELECT 1 FROM notification_log WHERE notification_id = ?", ("nid-near-cap",),
    )
    assert outbox_rows == []
