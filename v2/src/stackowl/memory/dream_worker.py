"""DreamWorker — nightly memory-consolidation JobHandler with checkpoint-resume.

The :class:`DreamWorkerCheckpoint` model lives in
:mod:`stackowl.memory.dream_worker_helpers` to keep the import graph acyclic
(B1). It is re-exported here for ergonomic ``from stackowl.memory.dream_worker
import DreamWorkerCheckpoint`` usage.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.memory.dream_worker_helpers import (
    PHASE_ORDER,
    DreamWorkerCheckpoint,
    PhaseName,
    advance_phase,
    finalize_run,
    load_committed_for_scan,
    mark_audit_contradictions,
    select_resumable_run,
)
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.db.pool import DbPool
    from stackowl.memory.contradiction_detector import (
        ContradictionDetector,
        ContradictionReport,
    )
    from stackowl.memory.conversation_miner import ConversationMiner
    from stackowl.memory.fact_promoter import FactPromoter
    from stackowl.memory.kuzu_sync_handler import KuzuSyncJobHandler
    from stackowl.memory.pruner import MemoryPruner
    from stackowl.memory.sqlite_bridge import SqliteMemoryBridge


class DreamWorkerJobHandler(JobHandler):
    """Nightly consolidation pass: contradict → promote → prune → kuzu_sync.

    Idempotency model: every phase transition is committed to
    ``dreamworker_runs`` *before* the next phase's work runs, so a crash
    mid-phase causes the next execution to resume cleanly from that phase.
    Each sub-step (:meth:`FactPromoter.promote_eligible`,
    :meth:`MemoryPruner.prune`, :meth:`KuzuSyncJobHandler.execute`) is
    already idempotent, so a re-run of a phase never double-counts.
    """

    _handler_name: ClassVar[str] = "dream_worker"

    def __init__(
        self,
        bridge: SqliteMemoryBridge,
        promoter: FactPromoter,
        pruner: MemoryPruner,
        kuzu_handler: KuzuSyncJobHandler,
        detector: ContradictionDetector,
        miner: ConversationMiner | None = None,
    ) -> None:
        # 1. ENTRY
        log.memory.debug("[memory] dream_worker.init: entry")
        self._bridge = bridge
        self._promoter = promoter
        self._pruner = pruner
        self._kuzu = kuzu_handler
        self._detector = detector
        self._miner = miner
        # 4. EXIT
        log.memory.debug("[memory] dream_worker.init: exit")

    @property
    def handler_name(self) -> str:
        return self._handler_name

    async def execute(self, job: Job) -> JobResult:
        """Run a full consolidation pass with checkpoint-resume semantics."""
        # 1. ENTRY
        log.memory.info(
            "[memory] dream_worker.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        TestModeGuard.assert_not_test_mode("dream_worker.execute")
        await self._mine()
        t0 = time.monotonic()
        db: DbPool = self._bridge._db  # JobHandler reuses the bridge's pool

        # 2. DECISION — resume in-flight run or start a fresh one
        resumed = await select_resumable_run(db)
        if resumed is not None:
            checkpoint = resumed
            log.memory.info(
                "[memory] dream_worker.execute: resuming run",
                extra={
                    "_fields": {
                        "run_id": checkpoint.run_id,
                        "phase": checkpoint.phase,
                    }
                },
            )
        else:
            checkpoint = await self._begin_new_run(db)

        try:
            checkpoint = await self._run_phases(db, checkpoint)
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000.0
            # B5
            log.memory.error(
                "[memory] dream_worker.execute: pass failed",
                exc_info=exc,
                extra={
                    "_fields": {
                        "job_id": job.job_id,
                        "run_id": checkpoint.run_id,
                        "phase": checkpoint.phase,
                    }
                },
            )
            return JobResult(
                job_id=job.job_id,
                success=False,
                output=None,
                error=f"{checkpoint.phase}: {exc}",
                duration_ms=duration_ms,
            )

        await finalize_run(db, checkpoint.run_id)
        duration_ms = (time.monotonic() - t0) * 1000.0
        output = (
            f"run_id={checkpoint.run_id} "
            f"promoted={checkpoint.facts_promoted} "
            f"pruned={checkpoint.facts_pruned} "
            f"contradictions={checkpoint.contradictions_found}"
        )
        # 4. EXIT
        log.memory.info(
            "[memory] dream_worker.execute: exit",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "run_id": checkpoint.run_id,
                    "promoted": checkpoint.facts_promoted,
                    "pruned": checkpoint.facts_pruned,
                    "contradictions": checkpoint.contradictions_found,
                    "duration_ms": duration_ms,
                }
            },
        )
        return JobResult(
            job_id=job.job_id,
            success=True,
            output=output,
            error=None,
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------ helpers

    async def _mine(self) -> int:
        """Mine staged conversation turns into staged facts. None-safe.

        Failure here must NOT abort the consolidation pass (self-heal) but must be
        LOUD (no hidden errors): logged at ERROR with context.
        """
        if self._miner is None:
            return 0
        try:
            return await self._miner.mine_all()
        except Exception as exc:
            log.memory.error(
                "[memory] dream_worker: conversation mining FAILED — consolidation continues",
                exc_info=exc,
            )
            return 0

    async def _begin_new_run(self, db: DbPool) -> DreamWorkerCheckpoint:
        run_id = str(uuid.uuid4())
        started_at = datetime.now(UTC).isoformat()
        await db.execute(
            "INSERT INTO dreamworker_runs (run_id, started_at, phase) VALUES (?, ?, ?)",
            (run_id, started_at, "contradiction"),
        )
        log.memory.info(
            "[memory] dream_worker.execute: started new run",
            extra={"_fields": {"run_id": run_id, "started_at": started_at}},
        )
        return DreamWorkerCheckpoint(
            run_id=run_id, started_at=started_at, phase="contradiction"
        )

    async def _run_phases(
        self, db: DbPool, checkpoint: DreamWorkerCheckpoint
    ) -> DreamWorkerCheckpoint:
        """Execute every phase from the checkpoint's position onward."""
        for phase in PHASE_ORDER:
            if PHASE_ORDER.index(phase) < PHASE_ORDER.index(checkpoint.phase):
                continue
            if phase == "complete":
                break
            log.memory.info(
                "[memory] dream_worker.execute: starting phase",
                extra={"_fields": {"run_id": checkpoint.run_id, "phase": phase}},
            )
            checkpoint = await self._run_one_phase(db, checkpoint, phase)
        return checkpoint

    async def _run_one_phase(
        self,
        db: DbPool,
        checkpoint: DreamWorkerCheckpoint,
        phase: PhaseName,
    ) -> DreamWorkerCheckpoint:
        """Run a single phase and return the updated checkpoint."""
        if phase == "contradiction":
            checkpoint = await self._phase_contradiction(db, checkpoint)
        elif phase == "promotion":
            checkpoint = await self._phase_promotion(checkpoint)
        elif phase == "pruning":
            checkpoint = await self._phase_pruning(checkpoint)
        elif phase == "kuzu_sync":
            checkpoint = await self._phase_kuzu_sync(checkpoint)
        # Advance to the next phase in the order
        next_phase: PhaseName = PHASE_ORDER[PHASE_ORDER.index(phase) + 1]
        checkpoint = await advance_phase(db, checkpoint, next_phase)
        log.memory.info(
            "[memory] dream_worker.execute: phase complete",
            extra={
                "_fields": {
                    "run_id": checkpoint.run_id,
                    "phase": phase,
                    "advanced_to": next_phase,
                }
            },
        )
        return checkpoint

    async def _phase_contradiction(
        self, db: DbPool, checkpoint: DreamWorkerCheckpoint
    ) -> DreamWorkerCheckpoint:
        facts = await load_committed_for_scan(db)
        reports = await self._detector.detect(list(facts))
        await self._record_contradictions(db, reports)
        return checkpoint.model_copy(
            update={
                "facts_processed": len(facts),
                "contradictions_found": len(reports),
            }
        )

    async def _phase_promotion(
        self, checkpoint: DreamWorkerCheckpoint
    ) -> DreamWorkerCheckpoint:
        promoted = await self._promoter.promote_eligible()
        return checkpoint.model_copy(update={"facts_promoted": promoted})

    async def _phase_pruning(
        self, checkpoint: DreamWorkerCheckpoint
    ) -> DreamWorkerCheckpoint:
        report = await self._pruner.prune()
        return checkpoint.model_copy(update={"facts_pruned": report.pruned_count})

    async def _phase_kuzu_sync(
        self, checkpoint: DreamWorkerCheckpoint
    ) -> DreamWorkerCheckpoint:
        kuzu_job = Job(
            job_id=f"dreamworker-kuzu-{checkpoint.run_id[:8]}",
            handler_name="kuzu_sync",
            schedule="manual",
            idempotency_key=f"dreamworker:{checkpoint.run_id}:kuzu_sync",
            last_run_at=None,
            next_run_at=datetime.now(UTC).isoformat(),
            status="pending",
        )
        await self._kuzu.execute(kuzu_job)
        return checkpoint

    async def _record_contradictions(
        self, db: DbPool, reports: list[ContradictionReport]
    ) -> None:
        await mark_audit_contradictions(db, reports)


__all__: list[str] = ["DreamWorkerJobHandler", "DreamWorkerCheckpoint"]
