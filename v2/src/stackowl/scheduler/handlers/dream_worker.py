"""Dream-worker registration — wires DreamWorkerJobHandler into the scheduler.

Provides :func:`register_dream_worker_handler` (factory used by the startup
orchestrator) and :func:`seed_dream_worker_schedule` (idempotent INSERT
that gives the JobScheduler an ``every <interval>m`` row to dispatch).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.db.pool import DbPool
    from stackowl.memory.contradiction_detector import ContradictionDetector
    from stackowl.memory.conversation_miner import ConversationMiner
    from stackowl.memory.dream_worker import DreamWorkerJobHandler
    from stackowl.memory.fact_promoter import FactPromoter
    from stackowl.memory.kuzu_sync_handler import KuzuSyncJobHandler
    from stackowl.memory.pruner import MemoryPruner
    from stackowl.memory.sqlite_bridge import SqliteMemoryBridge


# Default cadence in minutes when a caller doesn't pass an interval. The real
# value is config-driven (MemorySettings.dream_worker_interval_minutes) and
# threaded in by the assembly caller — this default keeps direct callers valid.
_DEFAULT_INTERVAL_MINUTES = 30
# BASE idempotency key only. The scheduler suffixes ``@<next_run_at>`` per
# occurrence (JobScheduler._occurrence_key), so a static "run once ever" key
# would wedge this recurring job in a permanent idempotent skip.
_DREAM_IDEMPOTENCY_KEY = "dream_worker"
_SELECT_EXISTING_SQL = (
    "SELECT job_id, schedule FROM jobs WHERE handler_name = ?"
)
_UPDATE_SCHEDULE_SQL = (
    "UPDATE jobs SET schedule = ?, next_run_at = ? WHERE handler_name = 'dream_worker'"
)
_INSERT_JOB_SQL = """
INSERT INTO jobs
    (job_id, handler_name, schedule, idempotency_key, last_run_at,
     next_run_at, status, retry_count, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def register_dream_worker_handler(
    bridge: SqliteMemoryBridge,
    promoter: FactPromoter,
    pruner: MemoryPruner,
    kuzu_handler: KuzuSyncJobHandler,
    detector: ContradictionDetector,
    miner: ConversationMiner | None = None,
    ann_k: int = 32,
    ann_threshold: int = 200,
) -> DreamWorkerJobHandler:
    """Construct and register the :class:`DreamWorkerJobHandler` singleton.

    Heavy memory modules are only imported inside the function body so the
    scheduler package stays cheap to import.
    """
    # 1. ENTRY
    log.heartbeat.debug("[scheduler] dream_worker handler: register entry")
    from stackowl.memory.dream_worker import DreamWorkerJobHandler

    handler = DreamWorkerJobHandler(
        bridge=bridge,
        promoter=promoter,
        pruner=pruner,
        kuzu_handler=kuzu_handler,
        detector=detector,
        miner=miner,
        ann_k=ann_k,
        ann_threshold=ann_threshold,
    )
    HandlerRegistry.instance().register(handler)
    # 4. EXIT
    log.heartbeat.info(
        "[scheduler] dream_worker handler registered",
        extra={"_fields": {"handler": handler.handler_name}},
    )
    return handler


async def seed_dream_worker_schedule(
    db: DbPool, interval_minutes: int = _DEFAULT_INTERVAL_MINUTES
) -> None:
    """Ensure the ``dream_worker`` job runs on the configured ``every <N>m`` cadence.

    * No existing row → INSERT a fresh ``every <interval>m`` job.
    * Existing row whose ``schedule`` differs from the configured value → REPAIR
      it in place (additive UPDATE), so a live ``daily@03:00`` row migrates to
      the new cadence. This is what makes the cadence config-driven across
      upgrades without a destructive migration.
    * Existing row already on the configured cadence → no-op.

    The idempotency key stays the base ``dream_worker`` — the scheduler suffixes
    the serviced occurrence per tick.
    """
    # 1. ENTRY
    schedule = f"every {interval_minutes}m"
    log.heartbeat.debug(
        "[scheduler] dream_worker schedule: seed entry",
        extra={"_fields": {"schedule": schedule}},
    )
    next_run_at = (
        datetime.now(UTC) + timedelta(minutes=interval_minutes)
    ).isoformat()
    existing = await db.fetch_all(_SELECT_EXISTING_SQL, ("dream_worker",))
    if existing:
        current = existing[0]["schedule"]
        if current == schedule:
            # 2. DECISION — already on the configured cadence.
            log.heartbeat.debug(
                "[scheduler] dream_worker schedule: already current — noop",
                extra={"_fields": {"schedule": schedule}},
            )
            return
        # 2. DECISION — repair a legacy/mismatched cadence in place.
        await db.execute(_UPDATE_SCHEDULE_SQL, (schedule, next_run_at))
        # 4. EXIT
        log.heartbeat.info(
            "[scheduler] dream_worker schedule repaired",
            extra={
                "_fields": {
                    "old_schedule": current,
                    "new_schedule": schedule,
                    "next_run_at": next_run_at,
                }
            },
        )
        return
    job_id = f"dream-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(UTC).isoformat()
    await db.execute(
        _INSERT_JOB_SQL,
        (
            job_id,
            "dream_worker",
            schedule,
            _DREAM_IDEMPOTENCY_KEY,
            None,
            next_run_at,
            "pending",
            0,
            now_iso,
        ),
    )
    # 4. EXIT
    log.heartbeat.info(
        "[scheduler] dream_worker schedule seeded",
        extra={"_fields": {"job_id": job_id, "schedule": schedule}},
    )


__all__: list[str] = [
    "register_dream_worker_handler",
    "seed_dream_worker_schedule",
]
