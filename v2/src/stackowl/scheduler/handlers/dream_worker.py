"""Dream-worker registration — wires DreamWorkerJobHandler into the scheduler.

Provides :func:`register_dream_worker_handler` (factory used by the startup
orchestrator) and :func:`seed_dream_worker_schedule` (idempotent INSERT
that gives the JobScheduler a daily-at-03:00 row to dispatch).
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


_DREAM_SCHEDULE = "daily@03:00"
_DREAM_IDEMPOTENCY_KEY = "dream_worker:nightly"
_SELECT_EXISTING_SQL = "SELECT job_id FROM jobs WHERE handler_name = ?"
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
    )
    HandlerRegistry.instance().register(handler)
    # 4. EXIT
    log.heartbeat.info(
        "[scheduler] dream_worker handler registered",
        extra={"_fields": {"handler": handler.handler_name}},
    )
    return handler


def _next_run_at_local_3am() -> str:
    """Return the next local-time 03:00 as an ISO8601 string (UTC)."""
    now = datetime.now()
    candidate = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC).isoformat()


async def seed_dream_worker_schedule(db: DbPool) -> None:
    """Insert a single ``dream_worker`` row into ``jobs`` if none exists.

    Idempotent: a second call is a no-op. Schedule is ``daily@03:00`` so the
    existing :func:`_compute_next_run` parser advances ``next_run_at``
    correctly on every completion.
    """
    # 1. ENTRY
    log.heartbeat.debug("[scheduler] dream_worker schedule: seed entry")
    existing = await db.fetch_all(_SELECT_EXISTING_SQL, ("dream_worker",))
    if existing:
        log.heartbeat.debug(
            "[scheduler] dream_worker schedule: already seeded — noop",
            extra={"_fields": {"row_count": len(existing)}},
        )
        return
    job_id = f"dream-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(UTC).isoformat()
    await db.execute(
        _INSERT_JOB_SQL,
        (
            job_id,
            "dream_worker",
            _DREAM_SCHEDULE,
            _DREAM_IDEMPOTENCY_KEY,
            None,
            _next_run_at_local_3am(),
            "pending",
            0,
            now_iso,
        ),
    )
    # 4. EXIT
    log.heartbeat.info(
        "[scheduler] dream_worker schedule seeded",
        extra={"_fields": {"job_id": job_id, "schedule": _DREAM_SCHEDULE}},
    )


__all__: list[str] = [
    "register_dream_worker_handler",
    "seed_dream_worker_schedule",
]
