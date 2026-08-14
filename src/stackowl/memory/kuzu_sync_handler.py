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

DEADLINE-BOUNDED (DEBT-19). Step 2 is an LLM call PER FACT, and the batch was
processed to completion however long that took. Measured on the live box:

  * 27,780 facts still unmirrored; ~828 sync per day, so ~34 days of backlog.
  * ``kuzu_sync`` started 42-49 times a DAY while contradiction/promotion/pruning
    started 1-5 — because the dream worker checkpoints its phase, and a phase
    that never returns is resumed forever.
  * 47 ERROR lines a day: "handler timed out — freed for retry/re-arm".

The handler was NOT failing to produce — it synced ~828 facts/day. It was failing
to RETURN, which pinned the dream worker's checkpoint at this phase and starved
the three phases after it. The dream worker's own mining budget had already
solved exactly this for mining — the later phases "need the other half...
starving it would preserve the very ratchet this fixes" — while this phase was
unbudgeted, so it consumed whatever mining left and then overran the timeout.
(That budget and those phases are gone as of D08.2: all five were fact work. The
history is kept because the DEADLINE below is still live and this is why it
exists.)

The fix is the same pattern: process facts until a budget expires, then RETURN
SUCCESS with partial progress. The backlog drains at the same rate — the run just
finishes, so the remaining phases get their turn and the log goes quiet.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import TYPE_CHECKING, ClassVar

from stackowl.infra.observability import log
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

#: Share of the scheduler's handler timeout this phase may consume.
#: Deliberately smaller than mining's 0.5: this handler is the LAST dream-worker
#: phase, so it inherits whatever the earlier phases already spent. A third
#: leaves headroom for the run to checkpoint and return rather than being killed
#: one fact short.
_SYNC_DEADLINE_SHARE = 0.33


def _sync_budget_s() -> float:
    """Seconds this handler may spend before deferring the rest to the next tick.

    Reads the scheduler's own timeout so a retune follows automatically instead
    of silently going stale — the exact rot that produced DEBT-19, where a budget
    calibrated against a smaller backlog was never revisited. Never raises: an
    unbounded default here is the defect, not the safe case.
    """
    try:
        from stackowl.scheduler.scheduler import _HANDLER_TIMEOUT_SEC

        return float(_HANDLER_TIMEOUT_SEC) * _SYNC_DEADLINE_SHARE
    except Exception as exc:  # never silent
        log.memory.error(
            "[memory] kuzu_sync_handler: could not read the handler timeout — "
            "using a conservative sync budget",
            exc_info=exc,
        )
        return 1200.0 * _SYNC_DEADLINE_SHARE


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

    async def execute(self, job: Job, *, budget_s: float | None = None) -> JobResult:
        """Sync the next batch of un-mirrored facts into Kuzu.

        ``budget_s`` — seconds this call may spend. The dream worker passes the
        run's REMAINING time; the scheduler (which calls ``execute(job)``) gets
        the conservative default share.

        A fixed share was the first attempt and it was WRONG, measured live: this
        is the last phase, so a fixed fraction ignores how much of the window the
        earlier phases actually used. On a run where mining had nothing to do,
        0.33 left ~800s unused and cut throughput roughly in half — below the rate
        at which new facts arrive, which would have turned a shrinking backlog
        into a growing one.
        """
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

        # 3. STEP — process each fact, BOUNDED BY A DEADLINE (DEBT-19).
        #
        # Each iteration is an LLM extract. Unbounded, a 50-fact batch outran the
        # scheduler's 1200s handler timeout, so the handler never returned and the
        # dream worker's checkpoint stayed pinned on this phase — resumed forever,
        # starving contradiction/promotion/pruning and logging 47 errors a day.
        # Stopping early and returning SUCCESS drains the backlog at the same rate
        # while letting the run actually finish.
        budget_s = _sync_budget_s() if budget_s is None else max(budget_s, 0.0)
        synced_count = 0
        entity_total = 0
        deferred = 0
        for index, row in enumerate(rows):
            elapsed = time.monotonic() - t0
            if elapsed >= budget_s:
                # Not an error: the remaining rows are still un-mirrored, so the
                # next tick picks them up unchanged. Logged at INFO because a
                # silently truncated batch is how a backlog hides.
                deferred = len(rows) - index
                log.memory.info(
                    "[memory] kuzu_sync_handler.execute: budget reached — deferring "
                    "the rest of this batch to the next tick",
                    extra={"_fields": {
                        "job_id": job.job_id, "synced": synced_count,
                        "deferred": deferred, "elapsed_s": round(elapsed, 1),
                        "budget_s": round(budget_s, 1),
                    }},
                )
                break
            fact_id = row["fact_id"]
            content = row["content"]
            entity_count = await self._sync_one_fact(fact_id, content)
            if entity_count >= 0:
                synced_count += 1
                entity_total += entity_count
            # 3. STEP — per-fact progress. The loop previously logged NOTHING
            # between entry and exit, so a run killed mid-batch left no record of
            # how far it got; the whole 20-minute window was invisible. Debug
            # level: one line per fact is too much for INFO, but "silent code is
            # undebuggable code" and this loop is where the time goes.
            log.memory.debug(
                "[memory] kuzu_sync_handler.execute: fact synced",
                extra={"_fields": {
                    "job_id": job.job_id, "fact_id": fact_id,
                    "entities": entity_count, "index": index + 1, "of": len(rows),
                }},
            )
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
                    "deferred": deferred,
                    "entities": entity_total,
                    "duration_ms": duration_ms,
                }
            },
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change",
            success=True,
            output=(
                f"synced_count={synced_count} entities={entity_total}"
                + (f" deferred={deferred}" if deferred else "")
            ),
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
