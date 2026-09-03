"""BriefSectionAssembler protocol + concrete implementations (Story 7.3).

Each assembler owns one section of the morning brief and exposes a single
``assemble(ctx) -> BriefSection`` coroutine. The orchestrator
(:class:`stackowl.scheduler.handlers.morning_brief.MorningBriefHandler`)
wraps every call in a ``try``/``except`` so a single failing source never
crashes the whole brief — failures become inline error sections.

Sections (in default render order):

* :class:`DateAndPrioritiesAssembler`  — ``date_and_priorities``
* :class:`AgentStatusAssembler`        — ``agent_status``
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict

from stackowl.brief.models import BriefSection
from stackowl.config.settings import Settings
from stackowl.infra.observability import log
from stackowl.sessions.models import MACHINE_LANE_PREFIXES, lane_family
from stackowl.skills import standard
from stackowl.tenancy.principal import DEFAULT_PRINCIPAL_ID

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.db.pool import DbPool
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


# ---------------------------------------------------------------------------
# 3. Pending staged-fact backlog
# ---------------------------------------------------------------------------


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


#: Telegram's hard per-message cap, in characters. A reply above it is SPLIT,
#: which is what breaks markdown entities across chunks and what made the Like
#: button fail — so "how many replies exceed one message" is the number worth
#: watching, not the raw average.
_ONE_TELEGRAM_MESSAGE = 4096

#: How far back the reply-length figure looks. Long enough to be stable across a
#: quiet day, short enough that a prompt change shows up within a week.
_REPLY_WINDOW_S = 7 * 24 * 3600


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
                "WHERE owner_id = ? AND source = 'learned' AND n_executions > 0",
                (DEFAULT_PRINCIPAL_ID,),
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
            f"WHERE owner_id = ? AND quality_score IS NOT NULL "
            f"AND lessons_arm IS NOT NULL "
            f"GROUP BY lessons_arm, lane",
            (DEFAULT_PRINCIPAL_ID,),
        )
        by_lane: dict[str, dict[str, tuple[int, float]]] = {}
        for r in arms:
            if r["q"] is None:
                continue
            by_lane.setdefault(str(r["lane"]), {})[str(r["arm"])] = (
                int(str(r["n"])), float(str(r["q"])),
            )
        # SUCCESS RATE — the comparison that is actually COMPARABLE across arms,
        # and the reason the quality line alone was misleading.
        #
        # The critic scorer selects `WHERE quality_score IS NULL AND success = 1
        # AND failure_class IS NULL`, so ONLY SUCCESSFUL TURNS ARE EVER SCORED.
        # The quality query above therefore averages over survivors of that gate,
        # and conditioning on success — which both the treatment and the turn's
        # quality cause — induces a spurious NEGATIVE association among them: if
        # lessons rescue marginal turns that would otherwise have failed, those
        # turns join the INJECTED scored pool and pull its mean down, while the
        # held-out pool keeps only turns good enough to succeed unaided.
        #
        # MEASURED 2026-08-24 over 6,890 arm-carrying rows: on success itself the
        # sign is the other way — held_out 23.9% vs injected 29.6%, z = -3.68.
        # Bakir's 13:00 brief that day, his first in 14 days, reported only the
        # quality line and so told him withholding lessons produced better work.
        #
        # NOTE the deliberate absence of `quality_score IS NOT NULL` here. Adding
        # it would reproduce the exact selection effect this line exists to avoid.
        succ_rows = await self._db.fetch_all(
            f"SELECT lessons_arm AS arm, "  # noqa: S608 — prefixes are module constants
            f"CASE WHEN {lane_case} THEN 'machine' ELSE 'interactive' END AS lane, "
            f"COUNT(*) AS n, SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS ok "
            f"FROM task_outcomes "
            f"WHERE owner_id = ? AND lessons_arm IS NOT NULL "
            f"GROUP BY lessons_arm, lane",
            (DEFAULT_PRINCIPAL_ID,),
        )
        succ_by_lane: dict[str, dict[str, tuple[int, int]]] = {}
        for r in succ_rows:
            succ_by_lane.setdefault(str(r["lane"]), {})[str(r["arm"])] = (
                int(str(r["n"])), int(str(r["ok"] or 0)),
            )

        for lane in ("interactive", "machine"):
            # Same both-arms guard as below: one side is not a comparison.
            succ = succ_by_lane.get(lane, {})
            if len(succ) == 2:
                i_n, i_ok = succ["injected"]
                h_n, h_ok = succ["held_out"]
                if i_n and h_n:
                    items.append(
                        f"lessons_success[{lane}] "
                        f"injected:{100 * i_ok / i_n:.1f}%(n={i_n}) "
                        f"held_out:{100 * h_ok / h_n:.1f}%(n={h_n})"
                    )
            scored = by_lane.get(lane, {})
            # Only once BOTH arms have scored turns: one side is not a
            # comparison, and printing it invites a conclusion from noise.
            if len(scored) != 2:
                continue
            inj_n, inj_q = scored["injected"]
            held_n, held_q = scored["held_out"]
            items.append(
                f"lessons_quality[{lane}] injected:{inj_q:.2f}(n={inj_n}) "
                f"held_out:{held_q:.2f}(n={held_n}) [scored turns only — "
                f"success-gated, not comparable across arms]"
            )

        # 3. STEP — are replies staying inside the budget the system prompt asks
        # for? Reported because the prompt can only ASK; nothing enforces it, so
        # without a number "we told the model to be brief" is an assumption.
        # Uses response_chars (migration 0109), NOT length(response_text), which
        # is truncated at 8,000 and therefore a floor rather than a measurement.
        reply = await self._db.fetch_all(
            "SELECT COUNT(*) AS n, AVG(response_chars) AS avg_len, "
            "SUM(response_chars > ?) AS over "
            "FROM task_outcomes WHERE owner_id = ? AND response_chars IS NOT NULL "
            "AND success = 1 AND captured_at > ?",
            # ORDER MATTERS: the `?` inside SUM(response_chars > ?) is bound
            # before the WHERE clause's, so the threshold comes first.
            (_ONE_TELEGRAM_MESSAGE, DEFAULT_PRINCIPAL_ID, time.time() - _REPLY_WINDOW_S),
        )
        if reply and reply[0]["n"]:
            r = reply[0]
            items.append(
                f"reply_len avg:{int(float(str(r['avg_len'])))} "
                f"over_{_ONE_TELEGRAM_MESSAGE}:{int(str(r['over'] or 0))}/{int(str(r['n']))}"
            )

        # 3. STEP — is the authoring standard actually holding? (D10.2 / slice 6.)
        #
        # THE FEEDBACK LEG. The validator refuses a non-conforming write, but a
        # refusal only covers skills authored SINCE it shipped — and D10.2's
        # acceptance is "zero new non-conforming skills", which is a claim about
        # a trend, not a state. Without a number here, "the standard is enforced"
        # is an assumption about code rather than an observation of the catalog.
        #
        # Checked against the CHEAP rules only — name shape and description
        # length. Parsing every body for section structure on each brief would
        # turn a two-query section into a filesystem walk, and these two catch
        # the failure that actually happened: 269 numbered duplicates and a
        # median description three times over the cap.
        #
        # The name rule is applied in PYTHON via standard.validate_name, not as
        # a SQL GLOB. A GLOB would be a second, weaker copy of "-N is forbidden"
        # — it cannot express "one or more digits" without enumerating widths,
        # so `foo-123` would quietly pass a check whose whole purpose is to
        # notice it. One copy of the rule, and the brief asks the module that
        # owns it. The catalog is a few hundred rows; reading the names is cheap.
        conf = await self._db.fetch_all(
            "SELECT name, LENGTH(description) AS desc_len FROM skills "
            "WHERE owner_id = ? AND lifecycle_state <> 'archived'",
            (DEFAULT_PRINCIPAL_ID,),
        )
        if conf:
            n_total = len(conf)
            numbered = sum(
                1 for r in conf if standard.validate_name(str(r["name"]))
            )
            long_desc = sum(
                1 for r in conf
                if int(str(r["desc_len"] or 0)) > standard.MAX_DESCRIPTION_CHARS
            )
            items.append(
                f"skill_standard v{standard.STANDARD_VERSION} "
                f"numbered:{numbered}/{n_total} "
                f"over_{standard.MAX_DESCRIPTION_CHARS}_chars:{long_desc}/{n_total}"
            )
            # A numbered name after D10.2 means the validator was BYPASSED — some
            # write path reaches the store without going through gated_skill_write.
            # That is a defect, not a backlog item, so it says so.
            if numbered:
                items.append(
                    f"! {numbered} numbered skill name(s) exist despite the "
                    f"standard forbidding them — a write path is bypassing the validator"
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
                "standard_version": standard.STANDARD_VERSION,
            }},
        )
        return BriefSection(key=self.key, title=self.key, items=items, omitted=False)


def now_iso_utc() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


#: How many lane families the spend section names before it stops. The tail is
#: summed into one line rather than dropped: a report that silently omits part of
#: the total teaches its reader to distrust the part it shows.
_MAX_SPEND_ROWS = 8

#: The window the report covers. Bakir asked for "every 24 hours", and a running
#: total would stop meaning anything by week two.
_SPEND_WINDOW_HOURS = 24.0


class SystemSpendAssembler:
    """Where the last 24 hours of tokens went, by kind of work.

    ASKED FOR, AFTER BEING SHOWN IT. On 2026-08-31 the platform spent 21,744,608
    input tokens: incident RCA 72.3%, goal 11.5%, reflection_writer 4.8%, and
    Bakir's own conversations 2.5%. Nothing reported that, so the only reason it
    was ever seen is that he asked on the night the bill got big enough to notice.
    His words: "that will give visibility to user what is happening in system."

    A SECTION, NOT A SECOND JOB. The morning brief already runs daily, already
    assembles sections under a per-section guard, and already delivers through the
    single ProactiveDeliverer seam with an honest per-channel status. Adding a
    reporting job beside it would be a second engine for a thing that exists.

    SHIPS ON: `_run_assembler` reads `toggles.get(key, True)`, so a section absent
    from settings.brief.sections is enabled with no operator switch to find.
    """

    key = "system_spend"

    def __init__(self, db: DbPool) -> None:
        self._db = db

    async def assemble(self, ctx: BriefContext) -> BriefSection:
        log.scheduler.debug("[brief] system_spend: entry", extra={"_fields": {}})
        since = datetime.now(UTC) - timedelta(hours=_SPEND_WINDOW_HOURS)
        try:
            # OWNER-SCOPED. cost_records is an owner-governed table (migration
            # 0043), and tests/tenancy/test_no_owner_scope_bypass.py fails the
            # build for any unscoped statement on one. This query shipped
            # unscoped on 2026-09-01 and the tripwire caught it — eight hours
            # later, because no run this loop makes had ever included
            # tests/tenancy. The brief is one principal's report; reading every
            # principal's spend into it would be a cross-tenant leak on a
            # multi-user deployment.
            rows = await self._db.fetch_all(
                "SELECT session_key, input_tokens FROM cost_records "
                "WHERE recorded_at > ? AND owner_id = ?",
                (since.isoformat(), DEFAULT_PRINCIPAL_ID),
            )
        except Exception as exc:  # B5 — answer honestly rather than raise
            # The handler would turn a raise into an error block; saying what is
            # missing is more useful to the reader than a stack summary.
            log.scheduler.error(
                "[brief] system_spend: could not read cost_records",
                exc_info=exc,
            )
            return BriefSection(
                key=self.key, title=self.key,
                items=["Token spend unavailable — the cost ledger could not be read."],
                omitted=False,
            )
        totals: dict[str, int] = {}
        for row in rows:
            family = lane_family(str(row["session_key"] or ""))
            totals[family] = totals.get(family, 0) + int(row["input_tokens"] or 0)
        grand = sum(totals.values())
        if not grand:
            # A quiet day still reports. Silence must be distinguishable from a
            # broken report — the same rule the brief's F-79 empty-recall follows.
            return BriefSection(
                key=self.key, title=self.key,
                items=[f"No model calls in the last {int(_SPEND_WINDOW_HOURS)}h."],
                omitted=False,
            )
        ordered = sorted(totals.items(), key=lambda kv: -kv[1])
        items = [f"Total input tokens (last {int(_SPEND_WINDOW_HOURS)}h): {grand:,}"]
        for family, tokens in ordered[:_MAX_SPEND_ROWS]:
            items.append(f"{family}: {tokens:,} ({100 * tokens / grand:.1f}%)")
        tail = ordered[_MAX_SPEND_ROWS:]
        if tail:
            rest = sum(t for _f, t in tail)
            items.append(
                f"everything else ({len(tail)} kinds): {rest:,} "
                f"({100 * rest / grand:.1f}%)"
            )
        log.scheduler.info(
            "[brief] system_spend: assembled",
            extra={"_fields": {"total_input_tokens": grand, "families": len(totals)}},
        )
        return BriefSection(key=self.key, title=self.key, items=items, omitted=False)


#: How far back the brief looks for concluded diagnoses. Matches the brief's own
#: daily cadence: a section reporting a window wider than the gap between briefs
#: would repeat itself, and one narrower would drop conclusions on the floor.
_INCIDENT_WINDOW_HOURS = 24


class ConcludedIncidentsAssembler:
    """What the self-heal loop diagnosed since the last brief.

    ASKED FOR, AFTER BEING SHOWN THE COST. On one day, 12 of 25 CRITICAL Telegram
    pages were "incident_escalation: RCA complete" — the loop telling Bakir it had
    finished a diagnosis. The sink it used hardcodes ``urgency="critical"``, and
    the notification router delivers every critical message immediately whatever
    the hour, so a concluded self-heal diagnosis interrupted him exactly as hard
    as a subsystem going down. Asked 2026-09-02, answered: "Digest only, page if
    unresolved."

    THIS IS THE DIGEST HALF, AND IT IS WHAT MAKES THE SILENCE SAFE. Removing the
    page without reporting the verdict anywhere would not be a digest, it would be
    deletion. ``record_diagnosis`` already writes an ``incident.diagnosed`` row for
    every conclusion, carrying the SAME composed text the page used to send, so
    this section reads a ledger that already exists rather than adding a store.

    A SECTION, NOT A SECOND JOB — the same reasoning as
    :class:`SystemSpendAssembler`: the brief already runs daily, already guards
    each section, and already delivers through one seam.

    SHIPS ON: ``_run_assembler`` reads ``toggles.get(key, True)``, so a section
    absent from ``settings.brief.sections`` is enabled with no switch to find.
    """

    key = "concluded_incidents"

    def __init__(self, db: DbPool) -> None:
        self._db = db

    async def assemble(self, ctx: BriefContext) -> BriefSection:
        log.scheduler.debug("[brief] concluded_incidents: entry", extra={"_fields": {}})
        since = time.time() - _INCIDENT_WINDOW_HOURS * 3600
        try:
            # audit_log is NOT owner-governed (no owner_id column, migration 0043
            # lists it as framework runtime), so there is no scope clause to add
            # here — checked against tests/tenancy rather than assumed.
            rows = await self._db.fetch_all(
                "SELECT actor, target, details, timestamp FROM audit_log "
                "WHERE event_type = ? AND timestamp >= ? ORDER BY timestamp DESC",
                ("incident.diagnosed", since),
            )
        except Exception as exc:
            log.scheduler.warning(
                "[brief] concluded_incidents: ledger read failed — omitting the "
                "section rather than reporting an empty one",
                exc_info=exc, extra={"_fields": {}},
            )
            return BriefSection(key=self.key, title=self.key, items=[], omitted=True)

        items: list[str] = []
        verified = unresolved = 0
        for row in rows:
            try:
                detail = json.loads(str(row["details"] or "{}"))
            except Exception:  # noqa: BLE001 — one bad row may not lose the rest
                detail = {}
            if detail.get("verified"):
                verified += 1
                summary = str(detail.get("summary") or "").strip()
                items.append(summary or f"{row['actor']}: concluded")
            else:
                unresolved += 1
        if unresolved:
            # COUNTED, NOT LISTED. 86 of 100 RCAs conclude verified=False, so
            # listing them would rebuild the noise this section exists to remove —
            # but hiding them entirely would misreport how much the loop is
            # actually resolving.
            items.append(
                f"{unresolved} analysed without reaching a confirmed cause"
            )
        log.scheduler.info(
            "[brief] concluded_incidents: assembled",
            extra={"_fields": {
                "verified": verified, "unresolved": unresolved,
                "window_hours": _INCIDENT_WINDOW_HOURS,
            }},
        )
        return BriefSection(
            key=self.key, title=self.key, items=items, omitted=not items,
        )


#: How far back the learning section looks. Matches the brief's daily cadence for
#: the same reason the incident section does: a wider window repeats itself, a
#: narrower one drops days on the floor.
_LEARNING_WINDOW_HOURS = 24

#: How many lesson texts are shown. MEASURED 2026-09-02: 49 lessons were written
#: in one 24-hour window. Listing them would be a wall nobody reads, and a brief
#: nobody reads reports nothing — so the section leads with counts and shows a
#: few of the most recent as evidence that the counts mean something.
_LEARNING_SAMPLES = 3


#: The window each half of the growth comparison covers. SEVEN DAYS, because the
#: measured signal moves on that scale — success rate went 28.9% -> 66.9% -> 89.5%
#: across three consecutive weeks — while a 24-hour window is mostly noise from
#: whatever he happened to ask that day.
_GROWTH_WINDOW_DAYS = 7

#: A capability needs this many failures in a week before its change is reported.
#: Below it, "2 failures became 1" reads as a 50% improvement and means nothing.
_GROWTH_MIN_FAILURES = 5


class GrowthAssembler:
    """What the platform got BETTER and WORSE at, week over week, in its own voice.

    N01 — his own idea, outside the reference map, 2026-08-10 verbatim:
    "i am not going to create a next chatbot which does no interaction. thats why
    i am thinking to build jarvis which will dream and rethink about his life, his
    abilities, his growing, learning, improving and etc things."

    NOTHING SAID ANY OF IT. The platform reflects on every TURN — 6,419 reflections
    promoted to 5,769 lessons — and evolves its own DNA (586 artifacts). What no
    surface did was compare itself to itself. AutonomicHealth reports current
    counts, Learning reports the last 24 hours, SystemSpend reports tokens; not one
    of them can say "I am better at this than I was".

    IT IS DETERMINISTIC ON PURPOSE. The obvious way to build "dreaming" is to hand
    a model its own history and let it narrate. That would produce a paragraph
    about growth whether or not any growth happened, which is the overclaim this
    platform has spent weeks learning not to make. The numbers below are measured
    and the sentence is assembled from them, so the brief cannot report improvement
    on a week that got worse.

    WHAT THE FIRST RUN HAD TO SAY, measured 2026-09-03: turn success 66.9% ->
    89.5%; browser_navigate failures 60 -> 18; web_fetch 27 -> 48 and shell 20 ->
    35, both worse; 22 skills learned against 0 the week before. A real
    self-assessment, including the two capabilities that regressed — which a
    narrated version would have been free to omit.

    A SECTION, NOT A NIGHTLY JOB. The old DreamWorker seat was deleted on
    2026-09-01 under "retired means deleted", so there is nothing to fill and
    nothing should be re-created: the brief already runs daily, guards each
    section, and delivers through one seam that is measured reaching him.
    """

    key = "growth"

    def __init__(self, db: DbPool) -> None:
        self._db = db

    async def _window(self, days_ago_start: int, days_ago_end: int) -> tuple[int, int]:
        """(turns, successes) in a window, owner-scoped."""
        now = time.time()
        rows = await self._db.fetch_all(
            "SELECT COUNT(*) AS tot, COALESCE(SUM(success), 0) AS ok FROM task_outcomes "
            "WHERE owner_id = ? AND captured_at > ? AND captured_at <= ?",
            (
                DEFAULT_PRINCIPAL_ID,
                now - days_ago_end * 86400.0,
                now - days_ago_start * 86400.0,
            ),
        )
        if not rows:
            return (0, 0)
        return (int(rows[0]["tot"] or 0), int(rows[0]["ok"] or 0))

    async def _failures_by_capability(
        self, days_ago_start: int, days_ago_end: int,
    ) -> dict[str, int]:
        now = time.time()
        rows = await self._db.fetch_all(
            # `success = 0` IS THE WHOLE CORRECTNESS OF THIS NUMBER. A row can
            # name a failed_capability on a turn that SUCCEEDED — the capability
            # stumbled and the recovery ladder got there anyway — and counting
            # those reports a regression that the user never experienced.
            # MEASURED 2026-09-03, hours after this section first shipped
            # counting every row: web_fetch read 27 -> 48, of which 22 and 37
            # were on turns that SUCCEEDED; the honest figures are 5 -> 11.
            # browser_navigate read 60 -> 18 against a true 12 -> 3, and `memory`
            # and `read_file` appeared as improvements on 2 and 1 real failures.
            # The direction survived for every capability; the magnitudes did not.
            "SELECT failed_capability AS cap, COUNT(*) AS n FROM task_outcomes "
            "WHERE owner_id = ? AND captured_at > ? AND captured_at <= ? "
            "AND failed_capability IS NOT NULL AND failed_capability <> '' "
            "AND success = 0 "
            "GROUP BY failed_capability",
            (
                DEFAULT_PRINCIPAL_ID,
                now - days_ago_end * 86400.0,
                now - days_ago_start * 86400.0,
            ),
        )
        return {str(r["cap"]): int(r["n"] or 0) for r in rows}

    async def assemble(self, ctx: BriefContext) -> BriefSection:
        log.scheduler.debug("[brief] growth: entry", extra={"_fields": {}})
        w = _GROWTH_WINDOW_DAYS
        try:
            this_tot, this_ok = await self._window(0, w)
            prior_tot, prior_ok = await self._window(w, 2 * w)
            this_f = await self._failures_by_capability(0, w)
            prior_f = await self._failures_by_capability(w, 2 * w)
            skills = await self._db.fetch_all(
                "SELECT COUNT(*) AS n FROM skills WHERE owner_id = ? AND loaded_at > ?",
                (DEFAULT_PRINCIPAL_ID, time.time() - w * 86400.0),
            )
            learned = int(skills[0]["n"] or 0) if skills else 0
        except Exception as exc:  # noqa: BLE001 — one section may not kill the brief
            log.scheduler.warning("[brief] growth: could not read — omitting", exc_info=exc)
            return BriefSection(key=self.key, title=self.key, items=[], omitted=True)

        # NO PRIOR WEEK, NO COMPARISON. A first week has nothing to be better than,
        # and inventing a baseline of zero would report spectacular growth forever.
        if prior_tot == 0 or this_tot == 0:
            log.scheduler.info(
                "[brief] growth: not enough history to compare — omitting",
                extra={"_fields": {"this_turns": this_tot, "prior_turns": prior_tot}},
            )
            return BriefSection(key=self.key, title=self.key, items=[], omitted=True)

        now_rate = this_ok / this_tot
        was_rate = prior_ok / prior_tot
        delta = now_rate - was_rate
        verb = "better" if delta > 0.01 else ("worse" if delta < -0.01 else "about the same")
        items = [
            f"I finished {now_rate:.0%} of what you asked this week, against "
            f"{was_rate:.0%} the week before — I am {verb} at my job "
            f"({this_tot} turns, {prior_tot} before).",
        ]

        improved, regressed = [], []
        for cap in sorted(set(this_f) | set(prior_f)):
            now_n, was_n = this_f.get(cap, 0), prior_f.get(cap, 0)
            if max(now_n, was_n) < _GROWTH_MIN_FAILURES:
                continue
            if now_n < was_n:
                improved.append(f"{cap} ({was_n}→{now_n})")
            elif now_n > was_n:
                regressed.append(f"{cap} ({was_n}→{now_n})")
        if improved:
            items.append("I stopped failing so often at: " + ", ".join(improved) + ".")
        # ALWAYS REPORTED WHEN PRESENT. A self-assessment that only lists progress
        # is the overclaim shape wearing a friendly voice.
        if regressed:
            items.append("I got WORSE at: " + ", ".join(regressed) + ".")
        items.append(
            f"I learned {learned} new skill(s) this week."
            if learned
            else "I learned no new skills this week."
        )

        log.scheduler.info(
            "[brief] growth: assembled",
            extra={"_fields": {
                "success_now": round(now_rate, 3), "success_prior": round(was_rate, 3),
                "improved": len(improved), "regressed": len(regressed),
                "skills_learned": learned,
            }},
        )
        return BriefSection(key=self.key, title=self.key, items=items, omitted=False)


class LearningAssembler:
    """What the platform actually learned since the last brief.

    D09.6 asks the reference platform's question — "what have I actually taught
    it?" — which that platform answers with a learning graph rendered in a desktop
    app. StackOwl has no desktop app; it has this brief, which is the surface an
    equivalent answer can reach.

    LEARNING IS HAPPENING AND NONE OF IT WAS VISIBLE. Measured 2026-09-02 on the
    live database: 5,747 lessons and 586 learning artifacts all-time, with 49
    lessons and 21 DNA adjustments written in the last 24 hours alone — and
    `note_applied_lesson` invoked 791 times, so they are read back and used. The
    only way to see any of it was to query SQLite by hand.

    COUNTS FIRST, THEN A FEW TEXTS. 49 entries a day cannot be listed; the counts
    say how much was learned and the samples show that the counts are not empty
    bookkeeping. The lesson bodies are already written for a reader — "What worked
    for rca_gatherer: ..." — so they need no reformatting.

    A SECTION, NOT A SECOND JOB, and the same reasoning as its two siblings: the
    brief already runs daily, already guards each section, and already delivers
    through one seam.
    """

    key = "learning"

    def __init__(self, db: DbPool) -> None:
        self._db = db

    async def assemble(self, ctx: BriefContext) -> BriefSection:
        log.scheduler.debug("[brief] learning: entry", extra={"_fields": {}})
        since = datetime.now(UTC) - timedelta(hours=_LEARNING_WINDOW_HOURS)
        items: list[str] = []
        by_kind: dict[str, int] = {}
        try:
            # `lessons` carries no owner_id (it is not owner-governed), so there is
            # no scope clause to add here — checked against the table, not assumed.
            rows = await self._db.fetch_all(
                "SELECT source_type, content FROM lessons WHERE created_at >= ? "
                "ORDER BY created_at DESC",
                (since.isoformat(),),
            )
        except Exception as exc:
            log.scheduler.error(
                "[brief] learning: could not read lessons — omitting the section "
                "rather than reporting that nothing was learned",
                exc_info=exc,
            )
            return BriefSection(key=self.key, title=self.key, items=[], omitted=True)

        for row in rows:
            kind = str(row["source_type"] or "other")
            by_kind[kind] = by_kind.get(kind, 0) + 1
        if by_kind:
            spread = ", ".join(
                f"{n} from {k}" for k, n in sorted(by_kind.items(), key=lambda kv: -kv[1])
            )
            items.append(f"{sum(by_kind.values())} lessons learned — {spread}")
            for row in rows[:_LEARNING_SAMPLES]:
                text = " ".join(str(row["content"] or "").split())
                if text:
                    items.append(text[:220])

        try:
            # learning_artifacts IS owner-governed (it carries owner_id), and
            # tests/tenancy fails the build for an unscoped statement on one.
            dna = await self._db.fetch_all(
                "SELECT artifact_type, COUNT(*) AS n FROM learning_artifacts "
                "WHERE created_at >= ? AND owner_id = ? GROUP BY artifact_type",
                (since.isoformat(), DEFAULT_PRINCIPAL_ID),
            )
            for row in dna:
                items.append(f"{row['n']} {row['artifact_type']} adjustments")
        except Exception as exc:
            # Partial is better than omitted: the lessons half already succeeded,
            # and dropping it because the second query failed would report less
            # than is known.
            log.scheduler.warning(
                "[brief] learning: could not read learning_artifacts — reporting "
                "the lessons half only",
                exc_info=exc,
            )

        log.scheduler.info(
            "[brief] learning: assembled",
            extra={"_fields": {
                "lessons": sum(by_kind.values()), "kinds": sorted(by_kind),
                "window_hours": _LEARNING_WINDOW_HOURS,
            }},
        )
        return BriefSection(
            key=self.key, title=self.key, items=items, omitted=not items,
        )
