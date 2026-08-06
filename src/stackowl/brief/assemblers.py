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

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict

from stackowl.brief.models import BriefSection
from stackowl.config.settings import Settings
from stackowl.infra.observability import log
from stackowl.sessions.models import MACHINE_LANE_PREFIXES

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.db.pool import DbPool
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.scheduler.scheduler import JobScheduler
    from stackowl.skills.store import SkillIndexStore


_MAX_HIGHLIGHT_CHARS = 120
_MAX_HIGHLIGHTS = 3
_MAX_PRIORITY_ROWS = 5
_RECALL_QUERY = "recent important facts"
# recall() is pure semantic top-K with NO recency filter — a vague query like
# _RECALL_QUERY can rank an old, generically "important"-sounding fact (e.g.
# stale world-news content from some earlier session) above anything from
# today, so the "last 24h" this class promises must be enforced client-side.
# Over-fetch a wider candidate pool so filtering to the window still leaves
# enough real recent facts to fill _MAX_HIGHLIGHTS.
_RECALL_CANDIDATE_POOL = 20
_HIGHLIGHT_WINDOW = timedelta(hours=24)
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
        candidates = await self._bridge.recall(_RECALL_QUERY, limit=_RECALL_CANDIDATE_POOL)
        cutoff = datetime.now(UTC) - _HIGHLIGHT_WINDOW
        records = [r for r in candidates if r.committed_at >= cutoff][:_MAX_HIGHLIGHTS]

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


# ---------------------------------------------------------------------------
# 5. Autonomic health (ADR-19) — the platform reporting on its own closed loops
# ---------------------------------------------------------------------------


class AutonomicHealthAssembler:
    """Fifth section — did the self-healing / self-improving loops do anything?

    ADR-19 measured four autonomic loops and found three of them running with no
    one looking. The single most expensive consequence was not any individual
    defect but the SILENCE: 409 RCAs discarded to a parser, 265 duplicate skills
    minted, and a catalog that was 92% dead weight — all of it discoverable only
    by someone running ad-hoc log queries at 2am, which is exactly how it WAS
    discovered.

    A loop nobody can see is a loop nobody maintains. This section costs two
    cheap queries and turns that from an archaeology exercise into a line in the
    brief the operator already reads.

    Deliberately DETERMINISTIC — counts from the database, no LLM, no
    interpretation. An assembler that summarised its findings in prose would be
    one more thing that can quietly start lying.
    """

    key: str = "autonomic_health"

    def __init__(self, skill_store: SkillIndexStore, db: DbPool) -> None:
        self._skills = skill_store
        self._db = db

    async def assemble(self, ctx: BriefContext) -> BriefSection:
        # 1. ENTRY
        log.scheduler.debug(
            "[brief] autonomic_health.assemble: entry",
            extra={"_fields": {"job_id": ctx.job_id}},
        )
        items: list[str] = []

        # 3. STEP — skill catalog shape (ADR-19 intervention #1's whole point).
        counts = await self._skills.lifecycle_counts()
        active = counts.get("active", 0)
        stale = counts.get("stale", 0)
        archived = counts.get("archived", 0)
        total = active + stale + archived
        if total:
            items.append(f"skills active:{active} stale:{stale} archived:{archived}")
            # The number that says whether the catalog is EARNING its size.
            used = await self._db.fetch_all(
                "SELECT COUNT(*) AS n FROM skills "
                "WHERE source = 'learned' AND n_executions > 0",
            )
            n_used = int(str(used[0]["n"])) if used else 0
            items.append(f"skills_ever_used:{n_used}/{total}")

        # 3. STEP — background job health over the last day. A failing autonomic
        # job is the failure mode that hides every other one: the loop stops and
        # its silence is indistinguishable from "nothing to report".
        rows = await self._db.fetch_all(
            "SELECT status, COUNT(*) AS n FROM job_results "
            "WHERE run_at >= datetime('now', '-1 day') GROUP BY status",
        )
        by_status = {str(r["status"]): int(str(r["n"])) for r in rows}
        ran = sum(by_status.values())
        failed = by_status.get("failed", 0)
        if ran:
            items.append(f"jobs_24h ran:{ran} failed:{failed}")
            if failed:
                worst = await self._db.fetch_all(
                    "SELECT job_id, COUNT(*) AS n FROM job_results "
                    "WHERE run_at >= datetime('now', '-1 day') AND status = 'failed' "
                    "GROUP BY job_id ORDER BY n DESC LIMIT 3",
                )
                for r in worst:
                    items.append(f"failing:{r['job_id']} x{r['n']}")

        # 3. STEP — ADR-19 #4: is lesson injection actually helping? Reported
        # here because an experiment nobody reads is precisely the failure this
        # ADR is about.
        #
        # SPLIT BY LANE, deliberately. Measured before shipping: over 30 days
        # there were 3,702 scored MACHINE-lane turns against 329 interactive
        # ones, at very different baselines (0.52 vs 0.39). A single aggregate
        # would be 92% background jobs — the interactive answer, which is the
        # one anybody cares about, would be invisible inside it, and any
        # difference in lane mix between the arms could flip the sign outright.
        lane_case = " OR ".join(
            f"session_key LIKE '{prefix}%'" for prefix in MACHINE_LANE_PREFIXES
        )
        arms = await self._db.fetch_all(
            f"SELECT lessons_arm AS arm, "  # noqa: S608 — prefixes are module constants
            f"CASE WHEN {lane_case} THEN 'machine' ELSE 'interactive' END AS lane, "
            f"COUNT(*) AS n, AVG(quality_score) AS q "
            f"FROM task_outcomes "
            f"WHERE quality_score IS NOT NULL AND lessons_arm IS NOT NULL "
            f"GROUP BY lessons_arm, lane",
        )
        by_lane: dict[str, dict[str, tuple[int, float]]] = {}
        for r in arms:
            if r["q"] is None:
                continue
            by_lane.setdefault(str(r["lane"]), {})[str(r["arm"])] = (
                int(str(r["n"])), float(str(r["q"])),
            )
        for lane in ("interactive", "machine"):
            scored = by_lane.get(lane, {})
            # Only once BOTH arms have scored turns: one side is not a
            # comparison, and printing it invites a conclusion from noise.
            if len(scored) != 2:
                continue
            inj_n, inj_q = scored["injected"]
            held_n, held_q = scored["held_out"]
            items.append(
                f"lessons_effect[{lane}] injected:{inj_q:.2f}(n={inj_n}) "
                f"held_out:{held_q:.2f}(n={held_n})"
            )

        # 2. DECISION — nothing measurable is a legitimate outcome, not an error.
        if not items:
            log.scheduler.debug(
                "[brief] autonomic_health.assemble: nothing measurable — omitting",
                extra={"_fields": {"job_id": ctx.job_id}},
            )
            return BriefSection(key=self.key, title=self.key, items=[], omitted=True)

        # 4. EXIT
        log.scheduler.info(
            "[brief] autonomic_health.assemble: exit",
            extra={"_fields": {
                "skills_active": active, "skills_stale": stale,
                "skills_archived": archived, "jobs_failed_24h": failed,
            }},
        )
        return BriefSection(key=self.key, title=self.key, items=items, omitted=False)


def now_iso_utc() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()
