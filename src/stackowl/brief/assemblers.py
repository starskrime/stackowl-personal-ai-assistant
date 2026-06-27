"""BriefSectionAssembler protocol + concrete implementations (Story 7.3).

Each assembler owns one section of the morning brief and exposes a single
``assemble(ctx) -> BriefSection`` coroutine. The orchestrator
(:class:`stackowl.scheduler.handlers.morning_brief.MorningBriefHandler`)
wraps every call in a ``try``/``except`` so a single failing source never
crashes the whole brief — failures become inline error sections.

Sections (in default render order):

* :class:`DateAndPrioritiesAssembler`  — ``date_and_priorities``
* :class:`MemoryHighlightsAssembler`   — ``memory_highlights``
* :class:`PendingStagedFactsAssembler` — ``pending_staged``
* :class:`AgentStatusAssembler`        — ``agent_status``
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict

from stackowl.brief.models import BriefSection
from stackowl.config.settings import Settings
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.db.pool import DbPool
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.scheduler.scheduler import JobScheduler


_MAX_HIGHLIGHT_CHARS = 120
_MAX_HIGHLIGHTS = 3
_MAX_PRIORITY_ROWS = 5
_RECALL_QUERY = "recent important facts"
# F-79 — an empty recall is surfaced as this explicit item rather than silently
# omitting the whole section. A single status literal (not a keyword/query
# word-list) so the rendered brief honestly shows the section ran and found
# nothing, instead of vanishing without a trace.
_NOTHING_NOTABLE_ITEM = "nothing notable"


class BriefContext(BaseModel):
    """Read-only context passed to every assembler."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    last_brief_time: str | None
    settings: Settings


@runtime_checkable
class BriefSectionAssembler(Protocol):
    """Protocol every concrete assembler must satisfy."""

    key: str

    async def assemble(self, ctx: BriefContext) -> BriefSection: ...


def _resolve_zone(settings: Settings) -> ZoneInfo:
    """Resolve the user's timezone, falling back to UTC on lookup failure."""
    tz_name = settings.system.timezone or "UTC"
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:  # B5 — never silent
        log.scheduler.warning(
            "[brief] assemblers._resolve_zone: invalid tz — falling back to UTC",
            exc_info=exc,
            extra={"_fields": {"tz_requested": tz_name}},
        )
        return ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# 1. Date + active priorities (pending goal_execution jobs)
# ---------------------------------------------------------------------------


class DateAndPrioritiesAssembler:
    """First section — current date/time and any pending goal_execution jobs."""

    key: str = "date_and_priorities"

    def __init__(self, db: DbPool) -> None:
        self._db = db

    async def assemble(self, ctx: BriefContext) -> BriefSection:
        # 1. ENTRY
        log.scheduler.debug(
            "[brief] date_and_priorities.assemble: entry",
            extra={"_fields": {"job_id": ctx.job_id}},
        )
        zone = _resolve_zone(ctx.settings)
        now_local = datetime.now(zone)
        items: list[str] = [f"now:{now_local.isoformat()}"]

        # 3. STEP — query pending goal_execution jobs as today's active priorities
        rows = await self._db.fetch_all(
            "SELECT job_id, schedule FROM jobs "
            "WHERE handler_name = ? AND status = ? "
            "ORDER BY next_run_at ASC LIMIT ?",
            ("goal_execution", "pending", _MAX_PRIORITY_ROWS),
        )
        for row in rows:
            items.append(f"goal:{row['job_id']}@{row['schedule']}")

        section = BriefSection(
            key=self.key,
            title=self.key,
            items=items,
            omitted=False,
        )
        # 4. EXIT
        log.scheduler.debug(
            "[brief] date_and_priorities.assemble: exit",
            extra={"_fields": {"item_count": len(items), "goal_rows": len(rows)}},
        )
        return section


# ---------------------------------------------------------------------------
# 2. Memory highlights — last-24h committed-fact recall
# ---------------------------------------------------------------------------


class MemoryHighlightsAssembler:
    """Second section — top committed facts from the last 24h."""

    key: str = "memory_highlights"

    def __init__(self, memory_bridge: MemoryBridge) -> None:
        self._bridge = memory_bridge

    async def assemble(self, ctx: BriefContext) -> BriefSection:
        # 1. ENTRY
        log.scheduler.debug(
            "[brief] memory_highlights.assemble: entry",
            extra={"_fields": {"job_id": ctx.job_id, "limit": _MAX_HIGHLIGHTS}},
        )
        records = await self._bridge.recall(_RECALL_QUERY, limit=_MAX_HIGHLIGHTS)

        # 2. DECISION — zero records → surface an explicit "nothing notable" item
        # (F-79) rather than silently omitting the section, and log at INFO (not
        # debug) so a chronically-empty highlights section is visible without
        # enabling debug logging. The section RENDERS (omitted=False).
        if not records:
            log.scheduler.info(
                "[brief] memory_highlights.assemble: no records — surfacing "
                "'nothing notable'",
                extra={"_fields": {"job_id": ctx.job_id, "query": _RECALL_QUERY}},
            )
            return BriefSection(
                key=self.key,
                title=self.key,
                items=[_NOTHING_NOTABLE_ITEM],
                omitted=False,
            )

        items = [r.content[:_MAX_HIGHLIGHT_CHARS] for r in records[:_MAX_HIGHLIGHTS]]
        # 4. EXIT
        log.scheduler.debug(
            "[brief] memory_highlights.assemble: exit",
            extra={"_fields": {"item_count": len(items)}},
        )
        return BriefSection(key=self.key, title=self.key, items=items, omitted=False)


# ---------------------------------------------------------------------------
# 3. Pending staged-fact backlog
# ---------------------------------------------------------------------------


class PendingStagedFactsAssembler:
    """Third section — count of staged facts awaiting promotion."""

    key: str = "pending_staged"

    def __init__(self, memory_bridge: MemoryBridge) -> None:
        self._bridge = memory_bridge

    async def assemble(self, ctx: BriefContext) -> BriefSection:
        # 1. ENTRY
        log.scheduler.debug(
            "[brief] pending_staged.assemble: entry",
            extra={"_fields": {"job_id": ctx.job_id}},
        )
        staged = await self._bridge.list_staged(status="staged")
        count = len(staged)

        # 2. DECISION — zero pending → omitted
        if count == 0:
            log.scheduler.debug(
                "[brief] pending_staged.assemble: no staged facts — omitting",
                extra={"_fields": {"job_id": ctx.job_id}},
            )
            return BriefSection(key=self.key, title=self.key, items=[], omitted=True)

        items = [f"staged_count:{count}"]
        # 4. EXIT
        log.scheduler.debug(
            "[brief] pending_staged.assemble: exit",
            extra={"_fields": {"count": count}},
        )
        return BriefSection(key=self.key, title=self.key, items=items, omitted=False)


# ---------------------------------------------------------------------------
# 4. Agent status (job-scheduler counts)
# ---------------------------------------------------------------------------


class AgentStatusAssembler:
    """Fourth section — counts of scheduler jobs by status."""

    key: str = "agent_status"

    def __init__(self, scheduler: JobScheduler) -> None:
        self._scheduler = scheduler

    async def assemble(self, ctx: BriefContext) -> BriefSection:
        # 1. ENTRY
        log.scheduler.debug(
            "[brief] agent_status.assemble: entry",
            extra={"_fields": {"job_id": ctx.job_id}},
        )
        jobs = await self._scheduler.list_jobs()
        scheduled = sum(1 for j in jobs if j.status == "pending" and j.enabled)
        paused = sum(1 for j in jobs if not j.enabled)
        failed = sum(1 for j in jobs if j.status == "failed" and j.enabled)

        items = [
            f"scheduled:{scheduled}",
            f"paused:{paused}",
            f"failed:{failed}",
        ]
        # 4. EXIT
        log.scheduler.debug(
            "[brief] agent_status.assemble: exit",
            extra={
                "_fields": {
                    "scheduled": scheduled,
                    "paused": paused,
                    "failed": failed,
                }
            },
        )
        return BriefSection(
            key=self.key,
            title=self.key,
            items=items,
            omitted=False,
        )


def now_iso_utc() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()
