"""The job and subsystem ``NameResolver``s (AD-30): wire ``scheduler`` INTO
``journal/narrator.py`` for ``RecordKind.JOB``/``HEAL``/``HEALTH`` (Story 2.6).

Mirrors ``pipeline/durable/journal_names.py``'s shape exactly: ``journal/``
imports nothing from any subsystem (AD-7), so the owning subsystem registers a
resolver INTO ``journal/`` rather than the other way round.
"""

from __future__ import annotations

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.journal import RecordKind, register_name_resolver
from stackowl.journal.job_events import _MAX_LABEL_LEN


async def resolve_job_name(db: DbPool, job_id: str) -> str | None:
    """Return a job's current ``handler_name``, or ``None`` if it no longer
    exists. A direct ``jobs`` read -- cheaper than building a full
    :class:`~stackowl.scheduler.scheduler.JobScheduler` and listing every row
    (``Scheduler.list_jobs()``) just to resolve one id.
    """
    log.journal.debug(
        "[journal_names] resolve_job_name: entry", extra={"_fields": {"job_id": job_id}},
    )
    rows = await db.fetch_all(
        "SELECT handler_name FROM jobs WHERE job_id = ?", (job_id,),
    )
    if not rows:
        # A finished one-shot's row is deleted moments after its own
        # `job.finished` event commits (AD-4's disclosed "target already gone
        # -> expired" contract) -- an expected miss, not an error.
        log.journal.debug(
            "[journal_names] resolve_job_name: job not found -- no name",
            extra={"_fields": {"job_id": job_id}},
        )
        return None
    name = str(rows[0]["handler_name"])[:_MAX_LABEL_LEN]
    log.journal.debug(
        "[journal_names] resolve_job_name: exit -- resolved",
        extra={"_fields": {"job_id": job_id}},
    )
    return name


def register_job_name_resolver(db_pool: DbPool) -> None:
    """Register the job ``NameResolver`` for :attr:`RecordKind.JOB`.

    Called once at boot (``startup/orchestrator.py``), alongside
    ``register_task_name_resolver``.
    """

    async def _resolver(job_id: str) -> str | None:
        return await resolve_job_name(db_pool, job_id)

    register_name_resolver(RecordKind.JOB, _resolver)
    log.journal.info("[journal_names] register_job_name_resolver: registered")


async def _resolve_subsystem_name(subsystem_id: str) -> str | None:
    """Identity passthrough -- the subsystem label already IS the display
    name a heal/health event's ``target_id`` carries (bounded, no DB lookup
    needed for narration)."""
    return subsystem_id[:_MAX_LABEL_LEN]


def register_subsystem_name_resolver() -> None:
    """Register the identity ``NameResolver`` for :attr:`RecordKind.HEAL` and
    :attr:`RecordKind.HEALTH`. No DB pool needed -- there is nothing to look up.
    """
    register_name_resolver(RecordKind.HEAL, _resolve_subsystem_name)
    register_name_resolver(RecordKind.HEALTH, _resolve_subsystem_name)
    log.journal.info(
        "[journal_names] register_subsystem_name_resolver: registered "
        "(heal, health)",
    )
