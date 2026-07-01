"""KuzuSyncJobHandler — mirrors committed facts into the Kuzu knowledge graph.

On each tick the handler:
  1. Loads committed_facts that haven't yet been mirrored (LEFT JOIN against
     ``kuzu_sync_log``), bounded to a configurable batch size.
  2. Runs :class:`EntityExtractor` on each fact's content.
  3. Upserts a Fact node, plus one Entity node + MENTIONS edge per
     extracted entity.
  4. Records the (fact_id, entity_count) pair in ``kuzu_sync_log`` so the
     fact is skipped on the next tick.

All Kuzu and LLM exceptions are caught per-fact so a single bad row never
poisons the batch.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import TYPE_CHECKING, ClassVar

from stackowl.infra.observability import log
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.db.pool import DbPool
    from stackowl.memory.entity_extractor import EntityExtractor, ExtractedEntity
    from stackowl.memory.kuzu_adapter import KuzuAdapter


_SELECT_UNSYNCED_SQL = """
SELECT cf.fact_id, cf.content
FROM committed_facts cf
LEFT JOIN kuzu_sync_log ksl ON ksl.fact_id = cf.fact_id
WHERE ksl.fact_id IS NULL
ORDER BY cf.committed_at DESC
LIMIT ?
"""

_INSERT_SYNC_LOG_SQL = """
INSERT OR REPLACE INTO kuzu_sync_log (fact_id, synced_at, entity_count)
VALUES (?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), ?)
"""


def _entity_id_for(fact_id: str, name: str, entity_type: str) -> str:
    """Deterministic content-addressed id for an entity within a fact."""
    digest = hashlib.sha256(f"{entity_type}|{name}".encode()).hexdigest()[:16]
    return f"ent_{digest}"


class KuzuSyncJobHandler(JobHandler):
    """Scheduled job that mirrors recent committed facts into Kuzu."""

    _handler_name: ClassVar[str] = "kuzu_sync"

    def __init__(
        self,
        kuzu_adapter: KuzuAdapter | None,
        entity_extractor: EntityExtractor | None,
        db: DbPool | None,
        batch_size: int = 50,
    ) -> None:
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu_sync_handler.init: entry",
            extra={"_fields": {"batch_size": batch_size}},
        )
        self._kuzu = kuzu_adapter
        self._extractor = entity_extractor
        self._db = db
        self._batch_size = batch_size
        # 4. EXIT
        log.memory.debug("[memory] kuzu_sync_handler.init: exit")

    @property
    def handler_name(self) -> str:
        return self._handler_name

    @property
    def defer_under_load(self) -> bool:
        return True  # Phase L — per-fact LLM extract + graph upserts; yield to turns

    async def execute(self, job: Job) -> JobResult:
        """Sync the next batch of un-mirrored facts into Kuzu."""
        # 1. ENTRY
        log.memory.info(
            "[memory] kuzu_sync_handler.execute: entry",
            extra={"_fields": {"job_id": job.job_id, "batch_size": self._batch_size}},
        )
        t0 = time.monotonic()

        # 2a. DECISION — graph layer degraded (DUR-5 / F069). When Kuzu failed to
        # initialise the adapter is None; the sync is a clean no-op so the
        # dream-worker kuzu phase (and the scheduler) succeed without the graph.
        if self._kuzu is None or self._db is None:
            duration_ms = (time.monotonic() - t0) * 1000.0
            log.memory.warning(
                "[memory] kuzu_sync_handler.execute: graph DEGRADED (None adapter) "
                "— skipping sync",
                extra={"_fields": {"job_id": job.job_id}},
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change",
                success=True,
                output="synced_count=0 graph_degraded",
                error=None,
                duration_ms=duration_ms,
            )

        # 2. DECISION — fetch un-mirrored fact batch
        try:
            rows = await self._db.fetch_all(
                _SELECT_UNSYNCED_SQL, (self._batch_size,)
            )
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000.0
            # B5
            log.memory.error(
                "[memory] kuzu_sync_handler.execute: fetch failed",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id}},
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change",
                success=False,
                output=None,
                error=f"fetch failed: {exc}",
                duration_ms=duration_ms,
            )

        if not rows:
            duration_ms = (time.monotonic() - t0) * 1000.0
            log.memory.info(
                "[memory] kuzu_sync_handler.execute: no unsynced facts",
                extra={"_fields": {"job_id": job.job_id}},
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change",
                success=True,
                output="synced_count=0",
                error=None,
                duration_ms=duration_ms,
            )

        # 3. STEP — process each fact
        synced_count = 0
        entity_total = 0
        for row in rows:
            fact_id = row["fact_id"]
            content = row["content"]
            entity_count = await self._sync_one_fact(fact_id, content)
            if entity_count >= 0:
                synced_count += 1
                entity_total += entity_count
            # F067 (C-5) — the Kuzu Connection is serialized onto ONE worker
            # thread, so a long sync batch could starve a live classify traverse.
            # Yield to the event loop between facts so interleaved traverse ops
            # get a turn at the executor queue (bounded head-of-line latency).
            await asyncio.sleep(0)

        duration_ms = (time.monotonic() - t0) * 1000.0
        # 4. EXIT
        log.memory.info(
            "[memory] kuzu_sync_handler.execute: exit",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "batch_rows": len(rows),
                    "synced": synced_count,
                    "entities": entity_total,
                    "duration_ms": duration_ms,
                }
            },
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change",
            success=True,
            output=f"synced_count={synced_count} entities={entity_total}",
            error=None,
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------ helpers

    async def _sync_one_fact(self, fact_id: str, content: str) -> int:
        """Mirror one fact + its extracted entities into Kuzu.

        Returns the number of entities written, or ``-1`` when the fact
        itself could not be persisted (so it stays un-mirrored for the
        next tick).
        """
        # Invariant: only reached on the non-degraded path — ``execute`` returns
        # early (a clean no-op) when any of these is None (DUR-5 / F069), so the
        # collaborators are guaranteed present here.
        assert self._kuzu is not None
        assert self._extractor is not None
        assert self._db is not None
        # 3. STEP — extract entities (returns [] on any failure)
        try:
            entities = await self._extractor.extract(content, fact_id)
        except Exception as exc:
            # B5 — extractor should not raise, but defend anyway
            log.memory.warning(
                "[memory] kuzu_sync_handler._sync_one_fact: extract raised",
                exc_info=exc,
                extra={"_fields": {"fact_id": fact_id}},
            )
            entities = []

        # 3. STEP — upsert the Fact node
        try:
            await self._kuzu.upsert_fact_node(fact_id, content, 1.0)
        except Exception as exc:
            # B5 — leave un-mirrored for retry
            log.memory.warning(
                "[memory] kuzu_sync_handler._sync_one_fact: fact upsert failed",
                exc_info=exc,
                extra={"_fields": {"fact_id": fact_id}},
            )
            return -1

        # 3. STEP — entities + edges
        written = await self._write_entities(fact_id, entities)

        # 3. STEP — record sync log
        try:
            await self._db.execute(_INSERT_SYNC_LOG_SQL, (fact_id, written))
        except Exception as exc:
            # B5
            log.memory.warning(
                "[memory] kuzu_sync_handler._sync_one_fact: sync_log write failed",
                exc_info=exc,
                extra={"_fields": {"fact_id": fact_id}},
            )
            return -1
        return written

    async def _write_entities(
        self, fact_id: str, entities: list[ExtractedEntity]
    ) -> int:
        """Upsert every entity + add a MENTIONS edge. Returns count succeeded."""
        # Invariant: unreachable on the degraded (None-adapter) path — see
        # ``_sync_one_fact`` / ``execute`` (DUR-5 / F069).
        assert self._kuzu is not None
        written = 0
        for entity in entities:
            entity_id = _entity_id_for(fact_id, entity.name, entity.entity_type)
            try:
                await self._kuzu.upsert_entity(
                    entity_id, entity.name, entity.entity_type, fact_id
                )
                await self._kuzu.link_fact_to_entity(fact_id, entity_id)
                written += 1
            except Exception as exc:
                # B5 — never let one entity poison the batch
                log.memory.warning(
                    "[memory] kuzu_sync_handler._write_entities: entity write failed",
                    exc_info=exc,
                    extra={
                        "_fields": {
                            "fact_id": fact_id,
                            "entity_id": entity_id,
                            "entity_type": entity.entity_type,
                        }
                    },
                )
        return written


__all__: list[str] = ["KuzuSyncJobHandler"]
