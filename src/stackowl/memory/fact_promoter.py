"""FactPromoter — promotes StagedFacts to committed_facts when gates pass."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING

from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.memory.sqlite_helpers import row_to_staged

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.db.pool import DbPool
    from stackowl.embeddings.registry import EmbeddingRegistry
    from stackowl.memory.lancedb_adapter import LanceDBAdapter
    from stackowl.memory.models import StagedFact


_SELECT_ELIGIBLE_SQL = """
SELECT fact_id, content, source_type, source_ref, confidence,
       staged_at, reinforcement_count, status, embedding, embedding_model, trust,
       scope_key
FROM staged_facts
WHERE status = 'staged'
  AND confidence >= ?
  AND (
        (source_type = 'conversation_fact' AND reinforcement_count >= ?)
     OR (source_type != 'conversation_fact' AND reinforcement_count >= ?)
  )
  AND staged_at <= ?
"""

_SELECT_BY_ID_SQL = """
SELECT fact_id, content, source_type, source_ref, confidence,
       staged_at, reinforcement_count, status, embedding, embedding_model, trust,
       scope_key
FROM staged_facts
WHERE fact_id = ?
"""

_INSERT_COMMITTED_SQL = """
INSERT OR IGNORE INTO committed_facts
    (fact_id, content, embedding, embedding_model, committed_at,
     source_type, source_ref, tags, trust, reinforcement_count, scope_key)
VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?, ?, ?, ?, ?, ?)
"""

_UPDATE_STAGED_STATUS_SQL = (
    "UPDATE staged_facts SET status = 'committed' WHERE fact_id = ?"
)

_INSERT_FTS_SQL = (
    "INSERT INTO committed_facts_fts(rowid, content) VALUES (?, ?)"
)

_SELECT_ROWID_SQL = "SELECT rowid AS rid FROM committed_facts WHERE fact_id = ?"


class FactPromoter:
    """Promotes :class:`StagedFact` items to ``committed_facts``.

    Dual-gate: ``confidence >= threshold`` **and**
    ``reinforcement_count >= required``. :meth:`force_promote` bypasses
    both gates and is intended for ``/staged approve``.
    """

    def __init__(
        self,
        db: DbPool,
        confidence_threshold: float = 0.8,
        reinforcement_required: int = 3,
        conversation_fact_reinforcement_required: int = 1,
        lancedb: LanceDBAdapter | None = None,
        clock: Clock | None = None,
        settle_minutes: int = 0,
        embedding_registry: EmbeddingRegistry | None = None,
    ) -> None:
        # 1. ENTRY
        log.memory.debug(
            "[memory] fact_promoter.init: entry",
            extra={
                "_fields": {
                    "confidence_threshold": confidence_threshold,
                    "reinforcement_required": reinforcement_required,
                    "conversation_fact_reinforcement_required": conversation_fact_reinforcement_required,
                    "has_lancedb": lancedb is not None,
                    "has_embedding_registry": embedding_registry is not None,
                    "settle_minutes": settle_minutes,
                }
            },
        )
        self._db = db
        self._confidence_threshold = confidence_threshold
        self._reinforcement_required = reinforcement_required
        self._conversation_fact_reinforcement_required = conversation_fact_reinforcement_required
        self._lancedb = lancedb
        self._embedding_registry = embedding_registry
        # Injected time source (ARCH-99) — never call datetime.now() directly so
        # the settle window is deterministically testable.
        self._clock: Clock = clock or WallClock()
        self._settle_minutes = settle_minutes
        # 4. EXIT
        log.memory.debug("[memory] fact_promoter.init: exit")

    def _settle_cutoff(self) -> str:
        """ISO-8601 (offset form) cutoff: facts staged at/before this are eligible.

        Matches the ``+00:00`` offset form written by the bridge — never a
        ``Z`` suffix — so lexicographic comparison in SQLite stays correct.
        """
        return (
            self._clock.now() - timedelta(minutes=self._settle_minutes)
        ).isoformat()

    def eligibility_params(self) -> dict[str, object]:
        """Expose the eligibility gate so the DreamWorker can mirror it exactly.

        Returns the same thresholds + settle cutoff that ``promote_eligible``
        binds, so outcome-verification counts *exactly* what would have promoted.
        """
        return {
            "confidence_threshold": self._confidence_threshold,
            "conversation_fact_reinforcement_required": (
                self._conversation_fact_reinforcement_required
            ),
            "reinforcement_required": self._reinforcement_required,
            "settle_cutoff": self._settle_cutoff(),
        }

    async def promote_eligible(self) -> int:
        """Scan ``staged_facts`` for promotion candidates. Returns count promoted."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] fact_promoter.promote_eligible: entry",
            extra={
                "_fields": {
                    "confidence_threshold": self._confidence_threshold,
                    "reinforcement_required": self._reinforcement_required,
                    "conversation_fact_reinforcement_required": self._conversation_fact_reinforcement_required,
                }
            },
        )
        # 2. DECISION — only facts settled past the window are eligible.
        cutoff = self._settle_cutoff()
        # 3. STEP — select eligible (conversation_fact uses lower threshold)
        rows = await self._db.fetch_all(
            _SELECT_ELIGIBLE_SQL,
            (
                self._confidence_threshold,
                self._conversation_fact_reinforcement_required,
                self._reinforcement_required,
                cutoff,
            ),
        )
        log.memory.debug(
            "[memory] fact_promoter.promote_eligible: candidates found",
            extra={"_fields": {"candidate_count": len(rows)}},
        )

        promoted = 0
        for row in rows:
            try:
                fact = row_to_staged(row)
                await self._promote_one(fact)
                promoted += 1
            except Exception as exc:
                # B5 — never skip a fact silently. NOTE: the returned `promoted`
                # count masks skipped rows (skipped == len(rows) - promoted); the
                # bool/int return signature is intentionally left unchanged here —
                # surfacing a (promoted, skipped) tuple is a larger caller-contract
                # change. Logged at ERROR so a skip is observable.
                log.memory.error(
                    "[memory] fact_promoter.promote_eligible: row failed — skipping",
                    exc_info=exc,
                    extra={"_fields": {"fact_id": row.get("fact_id")}},
                )

        # 4. EXIT
        log.memory.info(
            "[memory] fact_promoter.promote_eligible: exit",
            extra={"_fields": {"promoted_count": promoted, "candidates": len(rows)}},
        )
        return promoted

    async def force_promote(self, fact_id: str) -> bool:
        """Bypass both gates — promote a specific fact.

        Returns ``True`` if promoted, ``False`` if not found.
        """
        # 1. ENTRY
        log.memory.debug(
            "[memory] fact_promoter.force_promote: entry",
            extra={"_fields": {"fact_id": fact_id}},
        )
        rows = await self._db.fetch_all(_SELECT_BY_ID_SQL, (fact_id,))
        if not rows:
            # 2. DECISION — not found
            log.memory.warning(
                "[memory] fact_promoter.force_promote: fact_id not found",
                extra={"_fields": {"fact_id": fact_id}},
            )
            return False
        fact = row_to_staged(rows[0])
        try:
            await self._promote_one(fact)
        except Exception as exc:
            # Honor the documented bool contract — a per-row failure on the
            # interactive approve path must return False, not throw a raw
            # exception into the channel handler.
            log.memory.error(
                "[memory] fact_promoter.force_promote: promote failed",
                exc_info=exc,
                extra={"_fields": {"fact_id": fact_id}},
            )
            return False
        # 4. EXIT
        log.memory.info(
            "[memory] fact_promoter.force_promote: exit",
            extra={"_fields": {"fact_id": fact_id}},
        )
        return True

    # ------------------------------------------------------------------ helpers

    async def _promote_one(self, fact: StagedFact) -> None:
        from stackowl.memory.sqlite_helpers import pack_embedding

        # Self-heal: if the fact arrived without an embedding (e.g. miner-staged),
        # compute one now so it becomes SEMANTICALLY recallable.  Fail-open: when
        # no registry is wired or the embed call errors, vec is None and the fact
        # promotes FTS-only — identical to the pre-existing path.
        if fact.embedding is None and self._embedding_registry is not None:
            from stackowl.commands.memory_helpers import _best_effort_embed  # local import avoids cycle

            log.memory.debug(
                "[memory] fact_promoter._promote_one: computing missing embedding",
                extra={"_fields": {"fact_id": fact.fact_id}},
            )
            vec, model = await _best_effort_embed(fact.content, self._embedding_registry)
            if vec is not None:
                fact = fact.model_copy(update={"embedding": vec, "embedding_model": model})
                log.memory.debug(
                    "[memory] fact_promoter._promote_one: embedding computed",
                    extra={"_fields": {"fact_id": fact.fact_id, "model": model}},
                )

        embedding_blob = pack_embedding(fact.embedding) if fact.embedding else b""
        embedding_model = fact.embedding_model or ""
        # Commit the base row + its FTS index entry + the staged-status flip
        # ATOMICALLY (F070): INSERT committed -> read its rowid (visible to the
        # same txn before commit) -> INSERT fts, all in one transaction so a crash
        # mid-sequence can never leave committed_facts and committed_facts_fts
        # divergent. The rowid SELECT runs against the uncommitted INSERT inside
        # the txn's own connection.
        async with self._db.transaction() as tx:
            await tx.execute(
                _INSERT_COMMITTED_SQL,
                (
                    fact.fact_id,
                    fact.content,
                    embedding_blob,
                    embedding_model,
                    fact.source_type,
                    fact.source_ref,
                    json.dumps([]),
                    fact.trust,
                    # MEM-1 (F073) — carry the reinforcement the fact accrued
                    # while staged into the committed row, so blended recall can
                    # lift a repeatedly-confirmed preference.
                    fact.reinforcement_count,
                    # Phase 2 — carry the fact's scope (if any) from staged into
                    # committed, so recall(scope_key=...) can filter on it.
                    fact.scope_key,
                ),
            )
            await tx.execute(_UPDATE_STAGED_STATUS_SQL, (fact.fact_id,))
            # FTS5 sync — fetch the rowid we just inserted, then mirror content.
            async with tx.execute(_SELECT_ROWID_SQL, (fact.fact_id,)) as cursor:
                rowid_row = await cursor.fetchone()
            if rowid_row is not None:
                await tx.execute(
                    _INSERT_FTS_SQL,
                    (rowid_row[0], fact.content),
                )
        # Vector upsert — the committed fact must be SEMANTICALLY recallable, not
        # only FTS. The SQLite commit + FTS sync above are the source of truth, so
        # a LanceDB failure here is logged and swallowed; it MUST NOT abort the
        # promotion (recall degrades to the FTS fallback). Skip when the fact has
        # no embedding or no adapter was injected.
        if self._lancedb is not None and fact.embedding:
            try:
                await self._lancedb.upsert(
                    fact.fact_id,
                    fact.embedding,
                    {
                        "source_type": fact.source_type,
                        "source_ref": fact.source_ref,
                        "content": fact.content,
                        "trust": fact.trust,
                        # F062 — tag every vector with the model it was embedded
                        # under so recall can corpus-match and F063 can ANN-scope.
                        # Value already on the fact; never inferred. (dim authority
                        # is the corpus-identity sidecar, never read back here.)
                        "embedding_model": fact.embedding_model or "",
                    },
                )
            except Exception as exc:
                # B5 — log loudly, never abort the promotion.
                log.memory.warning(
                    "[memory] fact_promoter: LanceDB upsert failed — fact committed, semantic recall degraded",
                    exc_info=exc,
                    extra={"_fields": {"fact_id": fact.fact_id}},
                )
        log.memory.info(
            "[memory] fact_promoter: promoted",
            extra={
                "_fields": {
                    "fact_id": fact.fact_id,
                    "confidence": fact.confidence,
                    "reinforcement_count": fact.reinforcement_count,
                }
            },
        )
