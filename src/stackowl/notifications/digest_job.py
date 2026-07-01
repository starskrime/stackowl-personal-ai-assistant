"""NotificationDigestJob — flushes pending notification_queue rows (Story 7.4).

Registered with the scheduler under ``handler_name = "notification_digest"``.
On every execution the handler:

1. Selects all ``notification_queue`` rows whose ``scheduled_for <= now``.
2. For each row with a persisted body, transports it through the injected
   :class:`ProactiveDeliverer` (legacy rows without a body degrade to an
   audit-only flush). On a successful transport it writes a ``delivered``
   row to ``notification_log`` and deletes the queue row.
3. On a failed transport the row is NOT deleted and no ``delivered`` row is
   written — the body's ``scheduled_for`` is already ``<= now`` so the next
   tick retries it. Retries are BOUNDED: ``attempts`` is incremented each
   failure and the row is dead-lettered (``failed`` audit row + delete) past
   ``_MAX_FLUSH_ATTEMPTS`` so a permanently-bad channel cannot hot-loop or
   grow the queue without bound.

Delivery is **exactly-once** (STEER-4/F111): if an adapter's ``send_text``
reaches the user but then errors on the post-send response/delete path, the queue
row is retained, BUT a ``delivered`` ``notification_log`` row keyed by the
``notification_id`` was already written — so the next tick's idempotency guard
(:data:`_ALREADY_DELIVERED_SQL`) RECONCILES the leftover row (deletes it) without
a second transport. A genuinely-failed transport (no delivered log) still retries
under the bounded ``attempts`` cap. The ``notification_id`` is the idempotency key.
"""

from __future__ import annotations

import time as _time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.notifications.router_helpers import write_log_row
from stackowl.notifications.undelivered_outbox import UndeliveredOutbox
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.db.pool import DbPool
    from stackowl.notifications.deliverer import ProactiveDeliverer


_SELECT_DUE_SQL = (
    "SELECT notification_id, message_hash, urgency, category, channel, job_id, "
    "message, attempts "
    "FROM notification_queue WHERE scheduled_for <= ? ORDER BY scheduled_for ASC"
)
_DELETE_QUEUE_SQL = "DELETE FROM notification_queue WHERE notification_id = ?"
_BUMP_ATTEMPTS_SQL = "UPDATE notification_queue SET attempts = ? WHERE notification_id = ?"
# STEER-4/F111 exactly-once guard: a ``delivered`` notification_log row keyed by
# notification_id means the user ALREADY received this message (a prior tick
# transported it but failed on the post-send response/delete path, leaving the
# queue row). A re-tick must reconcile (delete the leftover row) WITHOUT a second
# transport. notification_id is the notification_log PRIMARY KEY (1 row per id).
_ALREADY_DELIVERED_SQL = (
    "SELECT 1 FROM notification_log "
    "WHERE notification_id = ? AND delivery_status = 'delivered' LIMIT 1"
)

# A row that fails transport this many times is dead-lettered (failed audit row
# + removed) so a permanently-bad channel cannot hot-loop or grow the queue.
_MAX_FLUSH_ATTEMPTS = 5


class NotificationDigestJob(JobHandler):
    """Flushes pending notification_queue rows and records deliveries."""

    def __init__(self, db: DbPool, deliverer: ProactiveDeliverer | None = None) -> None:
        self._db = db
        self._deliverer = deliverer
        # PB7b — the digest bypasses deliver()/router (it transports directly
        # below), so its own dead-letter branch is the only seam that can ever
        # see this drop; wire the durable NACK store here (mirrors
        # morning_brief.py's construction).
        self._outbox = UndeliveredOutbox(db)

    @property
    def handler_name(self) -> str:
        return "notification_digest"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        log.notifications.debug(
            "[notifications] digest.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        TestModeGuard.assert_not_test_mode("notification_digest.execute")
        t0 = _time.monotonic()
        now = datetime.now(UTC)

        # 2. DECISION — fetch all due rows
        try:
            rows = await self._db.fetch_all(_SELECT_DUE_SQL, (now.isoformat(),))
        except Exception as exc:  # B5 — never silent
            log.notifications.error(
                "[notifications] digest.execute: select failed",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id}},
            )
            duration_ms = (_time.monotonic() - t0) * 1000
            return JobResult(
                job_id=job.job_id,
                effect_class="delivery",
                success=False,
                output=None,
                error=str(exc),
                duration_ms=duration_ms,
                metadata={"flushed": 0},
            )

        log.notifications.debug(
            "[notifications] digest.execute: rows fetched",
            extra={"_fields": {"job_id": job.job_id, "due_count": len(rows)}},
        )

        # 3. STEP — flush each due row
        flushed = 0
        for row in rows:
            if await self._flush_row(row, now):
                flushed += 1

        duration_ms = (_time.monotonic() - t0) * 1000

        # 4. EXIT
        log.notifications.info(
            "[notifications] digest.execute: exit",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "flushed": flushed,
                    "due_count": len(rows),
                    "duration_ms": duration_ms,
                }
            },
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="delivery",
            success=True,
            output=f"flushed:{flushed}",
            error=None,
            duration_ms=duration_ms,
            metadata={"flushed": flushed, "due_count": len(rows)},
        )

    async def _flush_row(self, row: dict[str, object], now: datetime) -> bool:
        """Deliver-log + delete a single queued notification. Returns success."""
        notification_id = str(row["notification_id"])
        urgency = str(row["urgency"])
        category = str(row["category"])
        channel = str(row["channel"])
        message_hash = str(row["message_hash"])
        job_id_value = row.get("job_id")
        job_id = str(job_id_value) if job_id_value is not None else None
        body_value = row.get("message")
        body = str(body_value) if body_value is not None else None
        attempts_value = row.get("attempts")
        attempts = attempts_value if isinstance(attempts_value, int) else 0

        # STEER-4/F111 — exactly-once guard. If a ``delivered`` log row already
        # exists for this notification_id, a prior tick reached the user but failed
        # on the post-send path, leaving the queue row. RECONCILE (delete the
        # leftover row) WITHOUT a second transport or a duplicate log write — the
        # user must never receive the same batched notification twice. Self-healing
        # (B5): if the dedupe SELECT itself errors we fall through to the normal
        # path (at-least-once degradation is safer than dropping the message).
        try:
            already = await self._db.fetch_all(_ALREADY_DELIVERED_SQL, (notification_id,))
        except Exception as exc:  # B5 — never silent; degrade to at-least-once
            log.notifications.warning(
                "[notifications] digest._flush_row: dedupe check failed — proceeding",
                exc_info=exc,
                extra={"_fields": {"notification_id": notification_id}},
            )
            already = []
        if already:
            log.notifications.info(
                "[notifications] digest._flush_row: already delivered — reconciling, no re-send",
                extra={
                    "_fields": {
                        "notification_id": notification_id,
                        "channel": channel,
                        "message_hash": message_hash,
                    }
                },
            )
            try:
                await self._db.execute(_DELETE_QUEUE_SQL, (notification_id,))
            except Exception as exc:  # B5 — never silent
                log.notifications.warning(
                    "[notifications] digest._flush_row: reconcile delete failed",
                    exc_info=exc,
                    extra={"_fields": {"notification_id": notification_id}},
                )
                return False
            return True

        # Transport the stored body if present + a deliverer is wired; otherwise
        # degrade to audit-only (legacy rows without a body, or no deliverer).
        if body is not None and self._deliverer is not None:
            transport_status = await self._deliverer.transport(channel, body)
            if transport_status == "failed":
                # Do NOT write a "delivered" audit row here — that would lie in the
                # audit trail. The row's scheduled_for is already <= now, so the next
                # digest tick re-selects it. Retries are BOUNDED: past the cap the row
                # is dead-lettered (failed audit row + delete) so a permanently-bad
                # channel cannot hot-loop or grow the queue without bound.
                if attempts + 1 >= _MAX_FLUSH_ATTEMPTS:
                    log.notifications.error(
                        "[notifications] digest._flush_row: transport failed — "
                        "dead-lettering after max attempts",
                        extra={
                            "_fields": {
                                "notification_id": notification_id,
                                "channel": channel,
                                "message_hash": message_hash,
                                "attempts": attempts + 1,
                                "max_attempts": _MAX_FLUSH_ATTEMPTS,
                            }
                        },
                    )
                    await write_log_row(
                        self._db,
                        notification_id=notification_id,
                        urgency=urgency,
                        category=category,
                        channel=channel,
                        job_id=job_id,
                        status="failed",
                        created_at=now,
                        delivered_at=None,
                        message_hash=message_hash,
                    )
                    # PB7b — dead-lettering here bypasses deliver()/router
                    # entirely (this handler transports directly above), so
                    # this is the ONLY seam that ever sees this body. Without
                    # it the write_log_row above is hash-only audit and the
                    # body is gone the moment the queue row is deleted below.
                    await self._outbox.record_undelivered(
                        identity_key=DEFAULT_PRINCIPAL_ID,
                        body=body,
                        reason="transport_failed",
                        channel=channel,
                        category=category,
                        urgency=urgency,
                        job_id=job_id,
                    )
                    try:
                        await self._db.execute(_DELETE_QUEUE_SQL, (notification_id,))
                    except Exception as exc:  # B5 — never silent
                        log.notifications.warning(
                            "[notifications] digest._flush_row: dead-letter delete failed",
                            exc_info=exc,
                            extra={"_fields": {"notification_id": notification_id}},
                        )
                    return False
                log.notifications.warning(
                    "[notifications] digest._flush_row: transport failed — retaining row for retry",
                    extra={
                        "_fields": {
                            "notification_id": notification_id,
                            "channel": channel,
                            "message_hash": message_hash,
                            "attempts": attempts + 1,
                            "max_attempts": _MAX_FLUSH_ATTEMPTS,
                        }
                    },
                )
                try:
                    await self._db.execute(
                        _BUMP_ATTEMPTS_SQL, (attempts + 1, notification_id)
                    )
                except Exception as exc:  # B5 — never silent
                    log.notifications.warning(
                        "[notifications] digest._flush_row: attempt-bump failed",
                        exc_info=exc,
                        extra={"_fields": {"notification_id": notification_id}},
                    )
                return False
            log.notifications.info(
                "[notifications] digest._flush_row: transported",
                extra={
                    "_fields": {
                        "channel": channel,
                        "message_hash": message_hash,
                        "urgency": urgency,
                        "category": category,
                        "transport_status": transport_status,
                    }
                },
            )
        else:
            log.notifications.info(
                "[notifications] digest._flush_row: audit-only flush (no body)",
                extra={
                    "_fields": {
                        "channel": channel,
                        "message_hash": message_hash,
                        "urgency": urgency,
                        "category": category,
                        "has_deliverer": self._deliverer is not None,
                    }
                },
            )

        await write_log_row(
            self._db,
            notification_id=notification_id,
            urgency=urgency,
            category=category,
            channel=channel,
            job_id=job_id,
            status="delivered",
            created_at=now,
            delivered_at=now,
            message_hash=message_hash,
        )
        try:
            await self._db.execute(_DELETE_QUEUE_SQL, (notification_id,))
        except Exception as exc:  # B5 — never silent
            log.notifications.warning(
                "[notifications] digest._flush_row: delete failed",
                exc_info=exc,
                extra={"_fields": {"notification_id": notification_id}},
            )
            return False
        return True
