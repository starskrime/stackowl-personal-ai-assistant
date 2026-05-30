"""Pure helpers for :class:`CronjobTool` — recurrence rendering + cap counting.

Extracted so ``cronjob.py`` stays under the 300-line budget (B2). Everything
here is side-effect free except for structured logging; no clock, no I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.scheduler.job import Job
from stackowl.scheduler.scheduler_helpers import parse_every

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.db.pool import DbPool

_DEFAULT_OWL = "secretary"
_OWNER_SQL = "SELECT DISTINCT owl_name FROM conversations WHERE session_id = ? LIMIT 2"

# Tag written into a tool-created job's params so list/cap can find them again.
CREATED_BY_TAG = "cronjob"

# Approximate per-day frequency for the common shorthands, used only to render a
# human-readable "~Nx/day" hint so the model self-corrects over-scheduling.
_MINUTES_PER_DAY = 24 * 60


def render_recurrence(schedule: str) -> str:
    """Render a schedule expression in human terms (e.g. "~24x/day, forever").

    Best-effort and total: an unrecognised expression falls back to echoing the
    raw schedule rather than raising.
    """
    text = schedule.strip()
    lowered = text.lower()
    per_day: float | None = None

    if lowered.startswith("every "):
        rest = lowered[len("every ") :].strip()
        per_day = _per_day_from_every(rest)
    elif lowered.startswith("daily@"):
        per_day = 1.0
    else:
        per_day = _per_day_from_cron(text)

    if per_day is None:
        return f"runs on schedule '{text}', forever"
    if per_day >= 1:
        freq = f"~{round(per_day)}x/day"
    else:
        every_days = round(1 / per_day) if per_day > 0 else 0
        freq = f"~every {every_days} days" if every_days > 1 else "~daily"
    return f"runs {freq}, forever"


def _per_day_from_every(rest: str) -> float | None:
    """Parse an "every Nm" / "every Nh" shorthand into runs-per-day."""
    if not rest:
        return None
    unit = rest[-1]
    num_part = rest[:-1].strip()
    try:
        n = int(num_part)
    except ValueError:
        return None
    if n <= 0:
        return None
    if unit == "m":
        return _MINUTES_PER_DAY / n
    if unit == "h":
        return 24.0 / n
    return None


def _per_day_from_cron(expr: str) -> float | None:
    """Coarsely estimate runs-per-day for a 5-field cron expression."""
    fields = expr.split()
    if len(fields) != 5:
        return None
    minute = fields[0]
    hour = fields[1]
    try:
        if minute.startswith("*/"):
            step = int(minute[2:])
            return _MINUTES_PER_DAY / step if step > 0 else None
        if minute == "*":
            return float(_MINUTES_PER_DAY)
        if hour == "*":
            return 24.0
        return 1.0
    except ValueError:
        return None


def is_valid_schedule(schedule: str) -> bool:
    """Return True if ``schedule`` is parseable by the scheduler's validator.

    Mirrors ``compute_next_run``'s accepted forms (``daily@HH:MM``, ``every Nm``/
    ``every Nh`` shorthand, or a 5-field cron via croniter) but WITHOUT the +1d
    graceful fallback — so the tool can reject a malformed schedule with a
    structured error BEFORE persisting, rather than silently scheduling +1d.
    """
    text = schedule.strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith("daily@"):
        body = text[len("daily@") :]
        parts = body.split(":")
        try:
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            return False
        return 0 <= hour <= 23 and 0 <= minute <= 59
    if lowered.startswith("every "):
        # Single source of truth with ``compute_next_run`` (s/m/h/d) so the tool
        # never advertises a cadence the scheduler then mis-arms to +1d.
        return parse_every(text) is not None
    try:
        from croniter import croniter  # type: ignore[import-untyped]
    except ImportError as exc:  # B5 — dependency missing, fail closed but loud
        log.tool.warning(
            "cron_helpers.is_valid_schedule: croniter unavailable",
            exc_info=exc,
        )
        return False
    return bool(croniter.is_valid(text))


def job_summary(job: Job) -> dict[str, object]:
    """Render a single :class:`Job` as a JSON-safe summary for tool output."""
    return {
        "job_id": job.job_id,
        "goal": str(job.params.get("goal", "")),
        "schedule": job.schedule,
        "recurrence": render_recurrence(job.schedule),
        "enabled": job.enabled,
        "next_run_at": job.next_run_at,
    }


def count_owl_jobs(jobs: list[Job], owl: str) -> int:
    """Count active tool-created cron jobs owned by ``owl``.

    A job counts when it was created by this tool (``params['created_by']``)
    AND tagged with the caller owl (``params['owl']``). Used for the soft-cap
    nudge — only the caller's own scheduled jobs count against their budget.
    """
    count = 0
    for job in jobs:
        if (
            job.params.get("created_by") == CREATED_BY_TAG
            and job.params.get("owl") == owl
        ):
            count += 1
    log.tool.debug(
        "cron_helpers.count_owl_jobs: exit",
        extra={"_fields": {"owl": owl, "count": count}},
    )
    return count


def _owns(job: Job, owl: str) -> bool:
    """True iff ``job`` is a cron-tool job owned by ``owl`` (single source of truth)."""
    return (
        job.params.get("created_by") == CREATED_BY_TAG
        and job.params.get("owl") == owl
    )


def filter_owl_jobs(jobs: list[Job], owl: str) -> list[Job]:
    """Return only the tool-created cron jobs owned by ``owl`` (for list)."""
    return [job for job in jobs if _owns(job, owl)]


def find_owned_job(jobs: list[Job], job_id: str, owl: str) -> Job | None:
    """Resolve a job by id, but ONLY if ``owl`` owns it (no existence oracle).

    Returns the :class:`Job` when ``job_id`` exists AND is a cron-tool job owned
    by ``owl``; otherwise ``None`` — identically for a truly-missing job and for
    another owl's job, so a caller cannot probe for jobs they do not own
    (privilege-escalation guard). Ownership is resolved via the same predicate
    as :func:`filter_owl_jobs`/the soft cap.
    """
    for job in jobs:
        if job.job_id == job_id and _owns(job, owl):
            return job
    return None


async def resolve_owl(db: DbPool, session_id: str | None) -> str:
    """Derive the owning owl from the session, defaulting to ``secretary``.

    Same provenance the session-access tools use (``conversations.owl_name``);
    fail-soft to the default owl so a missing/ambiguous/error session still
    attributes the job rather than dropping the action.
    """
    if not session_id:
        return _DEFAULT_OWL
    try:
        rows = await db.fetch_all(_OWNER_SQL, (session_id,))
    except Exception as exc:  # B5 — never silent
        log.tool.warning(
            "cron_helpers.resolve_owl: owner lookup failed — defaulting",
            exc_info=exc,
            extra={"_fields": {"session_id": session_id}},
        )
        return _DEFAULT_OWL
    if len(rows) == 1:
        owner = rows[0].get("owl_name")
        if isinstance(owner, str) and owner:
            return owner
    return _DEFAULT_OWL
