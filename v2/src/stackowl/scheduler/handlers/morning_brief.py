"""MorningBriefHandler — multi-section structured morning brief (Story 7.3).

Replaces the pipeline-based one-shot LLM implementation with a deterministic,
data-driven assembly pipeline. Each section is owned by a concrete
:class:`BriefSectionAssembler`; assemblers run sequentially and a failure
in any one of them produces an inline ``section_error:`` entry instead of
crashing the whole brief.

Persistence + delivery side-effects:

* Inserts a row into ``job_results`` with the rendered brief as
  ``result_text`` so ``/agents log`` can surface it.
* Emits ``"morning_brief_delivered"`` on the :class:`EventBus` for
  any subscribers (notification routers, telemetry).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stackowl.brief.assemblers import (
    AgentStatusAssembler,
    BriefContext,
    BriefSectionAssembler,
    DateAndPrioritiesAssembler,
    MemoryHighlightsAssembler,
    PendingStagedFactsAssembler,
    now_iso_utc,
)
from stackowl.brief.models import BriefSection, MorningBrief
from stackowl.brief.renderer import BriefRenderer
from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool
    from stackowl.events.bus import EventBus
    from stackowl.integrations.registry import IntegrationRegistry
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.scheduler.scheduler import JobScheduler


_INSERT_JOB_RESULT_SQL = (
    "INSERT INTO job_results (job_id, run_at, status, result_text, duration_ms) "
    "VALUES (?, ?, ?, ?, ?)"
)
_ERROR_PREVIEW_CHARS = 80
_EVENT_DELIVERED = "morning_brief_delivered"


class MorningBriefHandler(JobHandler):
    """Assembles + renders the multi-section morning brief."""

    def __init__(
        self,
        memory_bridge: MemoryBridge,
        scheduler: JobScheduler,
        db: DbPool,
        event_bus: EventBus,
        settings: Settings,
        integration_registry: IntegrationRegistry | None = None,
    ) -> None:
        self._memory_bridge = memory_bridge
        self._scheduler = scheduler
        self._db = db
        self._event_bus = event_bus
        self._settings = settings
        self._integration_registry = integration_registry
        self._renderer = BriefRenderer()
        self._assemblers: list[BriefSectionAssembler] = [
            DateAndPrioritiesAssembler(db=db),
            MemoryHighlightsAssembler(memory_bridge=memory_bridge),
            PendingStagedFactsAssembler(memory_bridge=memory_bridge),
            AgentStatusAssembler(scheduler=scheduler),
        ]
        if integration_registry is not None:
            from stackowl.integrations.integration_assembler import IntegrationSectionAssembler

            self._assemblers.append(IntegrationSectionAssembler(integration_registry))

    @property
    def handler_name(self) -> str:
        return "morning_brief"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        log.scheduler.debug(
            "[scheduler] morning_brief.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        TestModeGuard.assert_not_test_mode("morning_brief.execute")

        t0 = time.monotonic()
        ctx = BriefContext(
            job_id=job.job_id,
            last_brief_time=await self._lookup_last_brief_time(),
            settings=self._settings,
        )
        section_toggles = self._settings.brief.sections
        delivery_channels = list(self._settings.brief.channels)

        # 2. DECISION — assemble each enabled section under a guard
        log.scheduler.debug(
            "[scheduler] morning_brief.execute: assembling sections",
            extra={"_fields": {"count": len(self._assemblers)}},
        )
        sections: list[BriefSection] = []
        for assembler in self._assemblers:
            section = await self._run_assembler(assembler, section_toggles, ctx)
            sections.append(section)

        # 3. STEP — build + render the brief
        brief = MorningBrief(
            sections=sections,
            generated_at=now_iso_utc(),
            delivery_channels=delivery_channels,
        )
        rendered = self._renderer.render(brief)
        log.scheduler.info(
            "[scheduler] morning_brief.execute: sections assembled",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "rendered_len": len(rendered),
                    "section_count": len(sections),
                }
            },
        )

        duration_ms = (time.monotonic() - t0) * 1000

        # 3. STEP — persist + emit delivery event
        await self._record_result(job.job_id, "completed", rendered, duration_ms)
        self._emit_delivered(brief, delivery_channels)

        # 4. EXIT
        log.scheduler.info(
            "[scheduler] morning_brief.execute: exit",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "section_count": len(sections),
                    "duration_ms": duration_ms,
                }
            },
        )
        return JobResult(
            job_id=job.job_id,
            success=True,
            output=rendered,
            error=None,
            duration_ms=duration_ms,
            metadata={
                "section_count": len(sections),
                "delivery_channels": delivery_channels,
                "rendered_len": len(rendered),
            },
        )

    # ---------------------------------------------------------------- helpers

    async def _run_assembler(
        self,
        assembler: BriefSectionAssembler,
        toggles: dict[str, bool],
        ctx: BriefContext,
    ) -> BriefSection:
        """Run a single assembler with isolation + the per-section toggle."""
        key = assembler.key
        enabled = toggles.get(key, True)
        if not enabled:
            log.scheduler.debug(
                "[scheduler] morning_brief._run_assembler: section disabled by settings",
                extra={"_fields": {"key": key}},
            )
            return BriefSection(key=key, title=key, items=[], omitted=True)
        try:
            return await assembler.assemble(ctx)
        except Exception as exc:  # B5 — never silent
            err_summary = str(exc) or exc.__class__.__name__
            log.scheduler.error(
                "[scheduler] morning_brief._run_assembler: assembler failed",
                exc_info=exc,
                extra={"_fields": {"key": key, "err_summary": err_summary[:_ERROR_PREVIEW_CHARS]}},
            )
            return BriefSection(
                key=key,
                title=key,
                items=[f"section_error:{err_summary[:_ERROR_PREVIEW_CHARS]}"],
                omitted=False,
            )

    async def _lookup_last_brief_time(self) -> str | None:
        """Return the ``run_at`` of the most recent successful morning brief, if any."""
        try:
            rows = await self._db.fetch_all(
                "SELECT run_at FROM job_results "
                "WHERE job_id LIKE ? AND status = ? "
                "ORDER BY run_at DESC LIMIT 1",
                ("morning_brief-%", "completed"),
            )
        except Exception as exc:  # B5 — never silent
            log.scheduler.warning(
                "[scheduler] morning_brief._lookup_last_brief_time: lookup failed",
                exc_info=exc,
            )
            return None
        return rows[0]["run_at"] if rows else None

    async def _record_result(
        self,
        job_id: str,
        status: str,
        result_text: str,
        duration_ms: float,
    ) -> None:
        """Insert a row into ``job_results``; warn-and-continue on failure."""
        run_at = datetime.now(UTC).isoformat()
        try:
            await self._db.execute(
                _INSERT_JOB_RESULT_SQL,
                (job_id, run_at, status, result_text, duration_ms),
            )
        except Exception as exc:  # B5 — never silent
            log.scheduler.warning(
                "[scheduler] morning_brief._record_result: insert failed",
                exc_info=exc,
                extra={"_fields": {"job_id": job_id, "status": status}},
            )
            return
        log.scheduler.debug(
            "[scheduler] morning_brief._record_result: written",
            extra={"_fields": {"job_id": job_id, "status": status, "run_at": run_at}},
        )

    def _emit_delivered(self, brief: MorningBrief, channels: list[str]) -> None:
        """Emit the ``morning_brief_delivered`` event for downstream subscribers."""
        payload = {
            "channels": channels,
            "status": "delivered",
            "section_count": len(brief.sections),
            "generated_at": brief.generated_at,
        }
        try:
            self._event_bus.emit(_EVENT_DELIVERED, payload)
        except Exception as exc:  # B5 — never silent
            log.scheduler.warning(
                "[scheduler] morning_brief._emit_delivered: emit failed",
                exc_info=exc,
                extra={"_fields": {"event": _EVENT_DELIVERED}},
            )
            return
        log.scheduler.debug(
            "[scheduler] morning_brief._emit_delivered: event emitted",
            extra={"_fields": {"event": _EVENT_DELIVERED, "channels": channels}},
        )
