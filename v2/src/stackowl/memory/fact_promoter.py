"""FactPromoter — promotes StagedFacts to committed_facts when gates pass."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.memory.sqlite_helpers import row_to_staged

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.db.pool import DbPool
    from stackowl.memory.lancedb_adapter import LanceDBAdapter
    from stackowl.memory.models import StagedFact


_SELECT_ELIGIBLE_SQL = """
SELECT fact_id, content, source_type, source_ref, confidence,
       staged_at, reinforcement_count, status, embedding, embedding_model
FROM staged_facts
WHERE status = 'staged'
  AND confidence >= ?
  AND (
        (source_type = 'conversation_fact' AND reinforcement_count >= ?)
     OR (source_type != 'conversation_fact' AND reinforcement_count >= ?)
  )
"""

_SELECT_BY_ID_SQL = """
SELECT fact_id, content, source_type, source_ref, confidence,
       staged_at, reinforcement_count, status, embedding, embedding_model
FROM staged_facts
WHERE fact_id = ?
"""

_INSERT_COMMITTED_SQL = """
INSERT OR IGNORE INTO committed_facts
    (fact_id, content, embedding, embedding_model, committed_at,
     source_type, source_ref, tags)
VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?, ?, ?)
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
                }
            },
        )
        self._db = db
        self._confidence_threshold = confidence_threshold
        self._reinforcement_required = reinforcement_required
        self._conversation_fact_reinforcement_required = conversation_fact_reinforcement_required
        self._lancedb = lancedb
        # 4. EXIT
        log.memory.debug("[memory] fact_promoter.init: exit")

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
        # 3. STEP — select eligible (conversation_fact uses lower threshold)
        rows = await self._db.fetch_all(
            _SELECT_ELIGIBLE_SQL,
            (
                self._confidence_threshold,
                self._conversation_fact_reinforcement_required,
                self._reinforcement_required,
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
                # B5 — never skip a fact silently
                log.memory.warning(
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
        await self._promote_one(fact)
        # 4. EXIT
        log.memory.info(
            "[memory] fact_promoter.force_promote: exit",
            extra={"_fields": {"fact_id": fact_id}},
        )
        return True

    # ------------------------------------------------------------------ helpers

    async def _promote_one(self, fact: StagedFact) -> None:
        from stackowl.memory.sqlite_helpers import pack_embedding

        embedding_blob = pack_embedding(fact.embedding) if fact.embedding else b""
        embedding_model = fact.embedding_model or ""
        await self._db.execute(
            _INSERT_COMMITTED_SQL,
            (
                fact.fact_id,
                fact.content,
                embedding_blob,
                embedding_model,
                fact.source_type,
                fact.source_ref,
                json.dumps([]),
            ),
        )
        await self._db.execute(_UPDATE_STAGED_STATUS_SQL, (fact.fact_id,))
        # FTS5 sync — fetch the rowid we just inserted, then mirror content
        rowid_rows = await self._db.fetch_all(_SELECT_ROWID_SQL, (fact.fact_id,))
        if rowid_rows:
            await self._db.execute(
                _INSERT_FTS_SQL,
                (rowid_rows[0]["rid"], fact.content),
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
