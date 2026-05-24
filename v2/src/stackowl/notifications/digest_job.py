"""NotificationDigestJob — flushes pending notification_queue rows (Story 7.4).

Registered with the scheduler under ``handler_name = "notification_digest"``.
On every execution the handler:

1. Selects all ``notification_queue`` rows whose ``scheduled_for <= now``.
2. Writes a ``delivered`` row to ``notification_log`` for each (the queued
   ``message_hash`` and ``urgency`` are preserved verbatim).
3. Deletes the flushed rows from ``notification_queue``.

Like :class:`NotificationRouter`, the handler never touches a real channel
adapter — channel transport lands in the channels epic.
"""

from __future__ import annotations

import time as _time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.notifications.router_helpers import write_log_row
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.db.pool import DbPool


_SELECT_DUE_SQL = (
    "SELECT notification_id, message_hash, urgency, category, channel, job_id "
    "FROM notification_queue WHERE scheduled_for <= ? ORDER BY scheduled_for ASC"
)
_DELETE_QUEUE_SQL = "DELETE FROM notification_queue WHERE notification_id = ?"


class NotificationDigestJob(JobHandler):
    """Flushes pending notification_queue rows and records deliveries."""

    def __init__(self, db: DbPool) -> None:
        self._db = db

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

        log.notifications.info(
            "[notifications] digest._flush_row: delivered",
            extra={
                "_fields": {
                    "channel": channel,
                    "message_hash": message_hash,
                    "urgency": urgency,
                    "category": category,
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
