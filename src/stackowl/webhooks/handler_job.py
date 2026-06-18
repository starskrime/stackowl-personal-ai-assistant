"""WebhookHandlerJob — scheduler handler that processes enqueued webhook events.

Registered with the global :class:`HandlerRegistry` under
``handler_name = "webhook_handler"``.  This story (7.5) ships the routing
plumbing only — Story 11 wires per-source business logic (GitHub PR review,
Stripe payment notifications, etc.).

The handler intentionally **never logs the event payload** — only the
``event_id`` and ``source`` fingerprint.  Bodies may contain PII or
attacker-controlled content.
"""

from __future__ import annotations

import time as _time
from typing import Any

from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult


class WebhookHandlerJob(JobHandler):
    """One-shot job handler that consumes a queued ``WebhookEvent``."""

    @property
    def handler_name(self) -> str:
        return "webhook_handler"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY — never logs the event payload
        t0 = _time.monotonic()
        event = self._extract_event(job)
        event_id = str(event.get("event_id", "")) if event else ""
        source = str(event.get("source", "")) if event else ""
        log.webhook.debug(
            "[webhook] handler.execute: entry",
            extra={"_fields": {"job_id": job.job_id, "event_id": event_id, "source": source}},
        )

        # 2. DECISION — fail fast on a malformed job (event missing)
        if not event:
            duration_ms = (_time.monotonic() - t0) * 1000
            log.webhook.warning(
                "[webhook] handler.execute: job missing 'event' params — marking failed",
                extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
            )
            return JobResult(
                job_id=job.job_id,
                success=False,
                output=None,
                error="webhook_handler: job.params['event'] missing",
                duration_ms=duration_ms,
                metadata={"event_id": event_id, "source": source},
            )

        # 3. STEP — placeholder; Story 11 dispatches to per-source business logic
        log.webhook.info(
            "[webhook] handler.execute: event processed (stub)",
            extra={"_fields": {"event_id": event_id, "source": source}},
        )

        # 4. EXIT
        duration_ms = (_time.monotonic() - t0) * 1000
        log.webhook.debug(
            "[webhook] handler.execute: exit",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "event_id": event_id,
                    "source": source,
                    "duration_ms": duration_ms,
                }
            },
        )
        return JobResult(
            job_id=job.job_id,
            success=True,
            output=f"event:{event_id}",
            error=None,
            duration_ms=duration_ms,
            metadata={"event_id": event_id, "source": source},
        )

    @staticmethod
    def _extract_event(job: Job) -> dict[str, Any]:
        """Return ``job.params['event']`` as a dict (empty if missing/invalid)."""
        raw = job.params.get("event") if job.params else None
        if isinstance(raw, dict):
            return raw
        return {}


# Self-register at import time so callers only need ``import stackowl.webhooks``.
HandlerRegistry.instance().register(WebhookHandlerJob())
