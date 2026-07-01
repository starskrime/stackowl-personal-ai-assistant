"""CheckInHandler — the scheduled wellbeing/check-in agent (C1 / F102).

Previously a permanent no-op that returned ``success=True`` and sent nothing — a
dishonest stub. It now assembles a LIGHT check-in body (a subset of the morning
brief's section assemblers) and delivers it through the SAME single seam +
:class:`DeliverySpec` resolver + :class:`DeliveryLedger` as the morning brief, to
the recipient persisted on the job row (a scheduled check-in has NO inbound
session, so the recipient MUST come from durable state — never ``_last_*``).

Honesty (no dressed-up give-up): if there is no resolvable recipient OR no body,
the result metadata records ``delivery_status="skipped"`` / the real outcome — it
is NEVER recorded as ``delivered``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from stackowl.brief.assemblers import (
    BriefContext,
    BriefSectionAssembler,
    DateAndPrioritiesAssembler,
    MemoryHighlightsAssembler,
    now_iso_utc,
)
from stackowl.brief.models import BriefSection, MorningBrief
from stackowl.brief.renderer import BriefRenderer
from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.notifications.proactive_job import (
    ProactiveDeliveryOutcome,
    job_success_for_rollup,
)
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.notifications.deliverer import ProactiveDeliverer
    from stackowl.notifications.delivery_ledger import DeliveryLedger
    from stackowl.scheduler.scheduler import JobScheduler

_CATEGORY = "check_in"
_ERROR_PREVIEW_CHARS = 80


class CheckInHandler(JobHandler):
    """Assembles a light check-in body and delivers it to the durable recipient."""

    def __init__(
        self,
        memory_bridge: MemoryBridge | None = None,
        scheduler: JobScheduler | None = None,
        db: DbPool | None = None,
        settings: Settings | None = None,
        proactive_deliverer: ProactiveDeliverer | None = None,
        delivery_ledger: DeliveryLedger | None = None,
    ) -> None:
        self._memory_bridge = memory_bridge
        self._scheduler = scheduler
        self._db = db
        self._settings = settings
        # The shared cron-born delivery loop (single seam + exactly-once ledger).
        from stackowl.notifications.proactive_job import ProactiveJobDeliverer

        self._job_deliverer = (
            ProactiveJobDeliverer(proactive_deliverer, delivery_ledger)
            if proactive_deliverer is not None and delivery_ledger is not None
            else None
        )
        # Lighter section set than the full brief — a check-in is a brief touch.
        self._assemblers: list[BriefSectionAssembler] = []
        if db is not None:
            self._assemblers.append(DateAndPrioritiesAssembler(db=db))
        if memory_bridge is not None:
            self._assemblers.append(
                MemoryHighlightsAssembler(memory_bridge=memory_bridge)
            )
        self._renderer = BriefRenderer()

    @property
    def handler_name(self) -> str:
        return "check_in"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        log.scheduler.debug(
            "[scheduler] check_in.execute: entry",
            extra={"_fields": {"job_id": job.job_id, "schedule": job.schedule}},
        )
        TestModeGuard.assert_not_test_mode("check_in.execute")
        t0 = time.monotonic()

        # 2. DECISION — assemble a light body. No body OR no deliverer => skip
        # HONESTLY (never a fake success-as-delivery).
        rendered = await self._assemble_body(job)
        if not rendered or self._job_deliverer is None:
            duration_ms = (time.monotonic() - t0) * 1000
            reason = "empty_body" if not rendered else "no_deliverer"
            log.scheduler.info(
                "[scheduler] check_in.execute: skipped (no send)",
                extra={"_fields": {"job_id": job.job_id, "reason": reason}},
            )
            return JobResult(
                job_id=job.job_id,
                success=True,
                output=None,
                error=None,
                duration_ms=duration_ms,
                metadata={"delivery_status": "skipped", "reason": reason},
            )

        # 3. STEP — deliver through the SAME seam as the morning brief; record the
        # honest aggregate (delivered only after a real transport success).
        outcome: ProactiveDeliveryOutcome = await self._job_deliverer.deliver_for_job(
            job, message=rendered, category=_CATEGORY
        )
        duration_ms = (time.monotonic() - t0) * 1000

        # An unresolved recipient => undeliverable => 'skipped' in the result
        # metadata (honest; not 'delivered').
        delivery_status = (
            "skipped" if outcome.rollup == "undeliverable" else outcome.rollup
        )

        # 4. EXIT
        log.scheduler.info(
            "[scheduler] check_in.execute: exit",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "status": delivery_status,
                    "duration_ms": duration_ms,
                }
            },
        )
        return JobResult(
            job_id=job.job_id,
            success=job_success_for_rollup(outcome.rollup),
            output=rendered,
            error=None,
            duration_ms=duration_ms,
            metadata={
                "delivery_status": delivery_status,
                "per_channel": outcome.per_channel,
                "undeliverable": list(outcome.undeliverable),
            },
        )

    async def _assemble_body(self, job: Job) -> str:
        """Render a light check-in body from the section assemblers (isolated)."""
        if self._settings is None or not self._assemblers:
            return ""
        ctx = BriefContext(
            job_id=job.job_id,
            last_brief_time=None,
            settings=self._settings,
        )
        sections: list[BriefSection] = []
        for assembler in self._assemblers:
            try:
                sections.append(await assembler.assemble(ctx))
            except Exception as exc:  # B5 — never silent; degrade to an error item
                err = str(exc) or exc.__class__.__name__
                log.scheduler.error(
                    "[scheduler] check_in._assemble_body: assembler failed",
                    exc_info=exc,
                    extra={
                        "_fields": {
                            "key": assembler.key,
                            "err": err[:_ERROR_PREVIEW_CHARS],
                        }
                    },
                )
                sections.append(
                    BriefSection(
                        key=assembler.key,
                        title=assembler.key,
                        items=[f"section_error:{err[:_ERROR_PREVIEW_CHARS]}"],
                        omitted=False,
                    )
                )
        brief = MorningBrief(
            sections=sections,
            generated_at=now_iso_utc(),
            delivery_channels=list(job.target_channels),
        )
        return self._renderer.render(brief)
