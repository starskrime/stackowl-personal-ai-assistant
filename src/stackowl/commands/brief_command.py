"""BriefCommand — ``/brief`` slash command (Story 7.3).

Triggers an on-demand morning brief in the current channel by invoking the
:class:`MorningBriefHandler` with a synthetic, non-persisted :class:`Job`.
The handler still writes its ``job_results`` row, so the manual brief
shows up in ``/agents log`` alongside scheduled deliveries.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.infra.observability import log
from stackowl.scheduler.job import Job

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.pipeline.state import PipelineState
    from stackowl.scheduler.handlers.morning_brief import MorningBriefHandler


_AD_HOC_SCHEDULE = "manual"
_EMPTY_FALLBACK = "(no brief content)"


class BriefCommand(SlashCommand):
    """``/brief`` — deliver the morning brief to the current channel now."""

    def __init__(self, handler: MorningBriefHandler | None = None) -> None:
        self._handler = handler

    @property
    def command(self) -> str:
        return "brief"

    @property
    def description(self) -> str:
        return "Deliver the morning brief to the current channel immediately."

    async def handle(self, args: str, state: PipelineState) -> str:
        if self._handler is None:
            return "✗ /brief: not configured"
        # 1. ENTRY
        log.gateway.debug(
            "[commands] brief.handle: entry",
            extra={
                "_fields": {
                    "args_len": len(args),
                    "channel": state.channel,
                    "session_id": state.session_id,
                }
            },
        )

        job = self._make_ad_hoc_job(state)
        # 2. DECISION — delegate the heavy lifting to the handler
        log.gateway.debug(
            "[commands] brief.handle: dispatching to handler",
            extra={"_fields": {"job_id": job.job_id}},
        )
        try:
            result = await self._handler.execute(job)
        except Exception as exc:  # B5 — never silent
            log.gateway.error(
                "[commands] brief.handle: handler raised",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id}},
            )
            return f"✗ /brief: {exc}"

        # 3. STEP — surface the rendered brief
        if not result.success:
            log.gateway.warning(
                "[commands] brief.handle: handler returned failure",
                extra={"_fields": {"job_id": job.job_id, "error": result.error}},
            )
            return f"✗ /brief: {result.error or 'unknown error'}"

        output = result.output or _EMPTY_FALLBACK
        # 4. EXIT
        log.gateway.info(
            "[commands] brief.handle: exit",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "output_len": len(output),
                    "section_count": result.metadata.get("section_count"),
                }
            },
        )
        return output

    def _make_ad_hoc_job(self, state: PipelineState) -> Job:
        """Construct a non-persisted :class:`Job` for manual ``/brief`` invocations."""
        job_id = f"brief-{uuid.uuid4().hex[:8]}"
        now_iso = datetime.now(UTC).isoformat()
        return Job(
            job_id=job_id,
            handler_name="morning_brief",
            schedule=_AD_HOC_SCHEDULE,
            idempotency_key=f"manual:{job_id}",
            last_run_at=None,
            next_run_at=now_iso,
            status="pending",
            primary_channel=state.channel,
            params={"trigger": "manual"},
        )

    @classmethod
    def create_and_register(cls, handler: MorningBriefHandler) -> BriefCommand:
        """Construct a :class:`BriefCommand` and register it on the singleton."""
        cmd = cls(handler=handler)
        CommandRegistry.instance().register(cmd)
        return cmd
