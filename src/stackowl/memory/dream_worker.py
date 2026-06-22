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
    count_committed_facts,
    count_stuck_eligible,
    finalize_run,
    get_contradiction_boundary_ids,
    get_contradiction_watermark,
    load_committed_for_scan,
    mark_audit_contradictions,
    mark_run_failed,
    record_stuck_eligible,
    retry_once_promotion,
    select_resumable_run,
    set_contradiction_watermark,
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
        ann_k: int = 32,
        ann_threshold: int = 200,
    ) -> None:
        # 1. ENTRY
        log.memory.debug("[memory] dream_worker.init: entry")
        self._bridge = bridge
        self._promoter = promoter
        self._pruner = pruner
        self._kuzu = kuzu_handler
        self._detector = detector
        self._miner = miner
        # F063 — incremental/ANN contradiction-scan tuning (>=32 keeps the >=0.85
        # cross-source band un-truncated; below threshold N the brute-force scan
        # stays behaviour-identical).
        self._ann_k = max(32, ann_k)
        self._ann_threshold = ann_threshold
        # 4. EXIT
        log.memory.debug("[memory] dream_worker.init: exit")

    @property
    def handler_name(self) -> str:
        return self._handler_name

    @property
    def defer_under_load(self) -> bool:
        return True  # Phase L — heavy memory pass (mine+phases+kuzu); yield to turns

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
            # Failure tracker — record the terminal status before surfacing.
            await mark_run_failed(
                db, checkpoint.run_id, phase=checkpoint.phase, error=str(exc)
            )
            return JobResult(
                job_id=job.job_id,
                success=False,
                output=None,
                error=f"{checkpoint.phase}: {exc}",
                duration_ms=duration_ms,
            )

        # Outcome verification — confirm eligible memories actually moved
        # short→long. Never fails the run (next cycle retries) but records loudly.
        await self._verify_outcome(db, checkpoint)

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

    async def _phase_reembed_on_model_drift(self, db: DbPool) -> int:
        """F066/F062 durable cure — rebuild the ANN corpus on embedding-model drift.

        When the corpus the LanceDB vectors were written under no longer matches
        the active embedding model (a model-pull / dim swap), recall is degraded
        to FTS by the F062 gate. This phase re-embeds the committed facts from the
        SQLite SoT, rebuilds the table at the active dim, and rewrites the
        sidecar — restoring semantic recall. Fail-safe: any error leaves the old
        corpus intact (recall stays on FTS) and is logged loudly, never raised.
        """
        # Defensive getattr — a minimal/test bridge may not expose the vector
        # surfaces; drift-cure is a no-op then (recall already FTS-only).
        lancedb = getattr(self._bridge, "lancedb", None)
        embeddings = getattr(self._bridge, "_embeddings", None)
        if lancedb is None or embeddings is None:
            return 0
        try:
            corpus_model, corpus_dim = await lancedb.corpus_identity()
            active_model = embeddings.active_model
            active_dim = embeddings.active_dim
            # Heal when the corpus drifted AND there are committed facts to embed.
            # F062-fix: gate on committed FACTS (the re-embed source is the SQLite
            # SoT text, NOT existing vectors) — NOT count_committed_with_vectors.
            # The old vectors-present gate left a legacy/untagged corpus (facts but
            # no vector blobs) drifting FOREVER: recall logged drift every turn but
            # the cure skipped because has_vectors==0. Now such a corpus is embedded
            # and tagged, curing the permanent FTS degrade.
            has_facts = await count_committed_facts(db) > 0
            drift = corpus_model != active_model or corpus_dim != active_dim
            if not drift or not has_facts:
                return 0
            from stackowl.memory.dream_worker_helpers import reembed_committed_facts

            log.memory.warning(
                "[memory] dream_worker.reembed_on_drift: embedding-model drift — "
                "rebuilding ANN corpus from SQLite SoT",
                extra={
                    "_fields": {
                        "corpus_model": corpus_model,
                        "corpus_dim": corpus_dim,
                        "active_model": active_model,
                        "active_dim": active_dim,
                    }
                },
            )

            async def _embed(texts: list[str]) -> list[list[float]]:
                vectors: list[list[float]] = await embeddings.get().embed(texts)
                return vectors

            return await reembed_committed_facts(
                db,
                lancedb,
                embed=_embed,
                active_model=active_model,
                active_dim=active_dim,
            )
        except Exception as exc:
            # B5 — never fail the consolidation pass on a reindex error.
            log.memory.error(
                "[memory] dream_worker.reembed_on_drift: failed — corpus left "
                "unchanged, recall stays on FTS",
                exc_info=exc,
            )
            return 0

    async def _phase_contradiction(
        self, db: DbPool, checkpoint: DreamWorkerCheckpoint
    ) -> DreamWorkerCheckpoint:
        # F066/F062 — cure any embedding-model drift BEFORE the scan so the
        # contradiction pass runs over a single-corpus, in-model set.
        await self._phase_reembed_on_model_drift(db)

        # F063 — choose the scan strategy by corpus size. Below the threshold the
        # brute-force O(n^2) scan is cheap and behaviour-identical (preserves the
        # cosine-clamp path). At/above it, switch to the incremental watermark +
        # ANN-candidate scan so we never reload the whole corpus each run.
        total = await count_committed_facts(db)
        lancedb = getattr(self._bridge, "lancedb", None)
        embeddings = getattr(self._bridge, "_embeddings", None)
        use_incremental = (
            total >= self._ann_threshold
            and lancedb is not None
            and embeddings is not None
        )

        if not use_incremental:
            # Brute-force fallback (small-N or no LanceDB) — unchanged behaviour.
            facts = await load_committed_for_scan(db)
            reports = await self._detector.detect(list(facts))
            await self._record_contradictions(db, reports)
            return checkpoint.model_copy(
                update={
                    "facts_processed": len(facts),
                    "contradictions_found": len(reports),
                }
            )

        # Incremental path — only new facts (since the watermark) are the LEFT
        # side; each gets an ANN search over the WHOLE corpus for the RIGHT side.
        watermark = await get_contradiction_watermark(db)
        boundary_ids = await get_contradiction_boundary_ids(db)
        new_facts = await load_committed_for_scan(
            db, since=watermark, exclude_ids=boundary_ids
        )
        if not new_facts:
            return checkpoint.model_copy(update={"facts_processed": 0})

        # use_incremental already guaranteed both are non-None; assert for the
        # type-checker (and as a defensive invariant).
        assert lancedb is not None and embeddings is not None
        active_model = embeddings.active_model
        escaped = active_model.replace("'", "''")
        ann_k = self._ann_k

        async def _neighbour_lookup(fact: object) -> list[object]:
            embedding = getattr(fact, "embedding", None) or []
            if not embedding:
                return []
            hits = await lancedb.search(
                list(embedding),
                limit=ann_k,
                filter_expr=f"embedding_model = '{escaped}'",
            )
            if not hits:
                return []
            from stackowl.memory.sqlite_helpers import fetch_committed_by_ids

            return list(
                await fetch_committed_by_ids(db, [h.fact_id for h in hits])
            )

        reports = await self._detector.detect_incremental(
            list(new_facts), _neighbour_lookup  # type: ignore[arg-type]
        )
        await self._record_contradictions(db, reports)

        # Advance the watermark to the newest scanned fact — ONLY after the scan
        # results are recorded above. A crash BEFORE this re-scans the same window
        # next pass (cheap re-work) rather than skipping unscanned facts, which
        # would be a permanent contradiction blind spot. committed_at is ms-precise
        # and SQLite 'now' is constant within a statement, so we use a >= scan and
        # persist the boundary fact_ids (those AT the newest ms) to exclude next
        # run — a same-ms fact is then scanned without re-emitting the boundary
        # pair. Both move atomically, so crash-safety is preserved.
        newest = max(f.committed_at for f in new_facts)
        boundary_at_newest = [
            f.fact_id for f in new_facts if f.committed_at == newest
        ]
        await set_contradiction_watermark(
            db, newest.isoformat(), boundary_ids=boundary_at_newest
        )

        return checkpoint.model_copy(
            update={
                "facts_processed": len(new_facts),
                "contradictions_found": len(reports),
            }
        )

    async def _phase_promotion(
        self, checkpoint: DreamWorkerCheckpoint
    ) -> DreamWorkerCheckpoint:
        # Bounded retry-once: a transient promotion failure self-heals; a second
        # failure re-raises so the failure path records status='failed'.
        promoted = await retry_once_promotion(self._promoter.promote_eligible)
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

    async def _verify_outcome(
        self, db: DbPool, checkpoint: DreamWorkerCheckpoint
    ) -> None:
        """Confirm eligible memories actually moved short→long this pass.

        Mirrors the promoter's eligibility gate (incl. the settle cutoff) to
        COUNT rows still ``status='staged'``. If any remain: record the count on
        the run, re-promote once, and re-query. If still stuck, write a loud
        loud ERROR is emitted. The run is NOT failed for stuck memories — the
        next cadence cycle retries — but the signal is recorded on the run row
        (``dreamworker_runs.stuck_eligible``) and surfaced at ERROR level.
        """
        # 1. ENTRY — the promoter must describe its gate so we mirror it exactly.
        params_fn = getattr(self._promoter, "eligibility_params", None)
        if params_fn is None:
            # No-hidden-errors: a promoter that can't describe its gate can't be
            # outcome-verified. Log LOUDLY and skip (the run still completes).
            log.memory.warning(
                "[memory] dream_worker.verify_outcome: promoter exposes no "
                "eligibility_params — skipping outcome verification",
                extra={"_fields": {"run_id": checkpoint.run_id}},
            )
            return
        params = params_fn()
        stuck = await count_stuck_eligible(db, **params)
        if stuck == 0:
            log.memory.debug(
                "[memory] dream_worker.verify_outcome: no stuck eligible — ok",
                extra={"_fields": {"run_id": checkpoint.run_id}},
            )
            return
        # 2. DECISION — eligible memories did not move; retry promotion once.
        log.memory.warning(
            "[memory] dream_worker.verify_outcome: eligible memories not promoted — retrying",
            extra={"_fields": {"run_id": checkpoint.run_id, "stuck": stuck}},
        )
        await record_stuck_eligible(db, checkpoint.run_id, stuck)
        await self._promoter.promote_eligible()
        # Re-query with a FRESH cutoff (clock may have advanced) — re-derive params.
        remaining = await count_stuck_eligible(db, **params_fn())
        await record_stuck_eligible(db, checkpoint.run_id, remaining)
        if remaining > 0:
            # 3. STEP — still stuck after the retry: record loudly (do not fail).
            # The count is persisted on the run row (record_stuck_eligible above);
            # here we surface the unresolved signal at ERROR so it is impossible
            # to miss. No audit_log write: the audit chain has a single canonical
            # writer (AuditLogger.append) and the run-row + ERROR fully satisfy
            # the "record loudly + tracker" requirement.
            log.memory.error(
                "[memory] dream_worker.verify_outcome: eligible memories still stuck "
                "after retry",
                extra={"_fields": {"run_id": checkpoint.run_id, "stuck_eligible": remaining}},
            )
        # 4. EXIT
        log.memory.info(
            "[memory] dream_worker.verify_outcome: exit",
            extra={
                "_fields": {
                    "run_id": checkpoint.run_id,
                    "stuck_before": stuck,
                    "stuck_after": remaining,
                }
            },
        )


__all__: list[str] = ["DreamWorkerJobHandler", "DreamWorkerCheckpoint"]
