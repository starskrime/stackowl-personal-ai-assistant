"""FactReinforcer — increments reinforcement_count for similar staged facts."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from stackowl.infra.observability import log
from stackowl.memory.sqlite_helpers import unpack_embedding

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.db.pool import DbPool
    from stackowl.embeddings.registry import EmbeddingRegistry


_SELECT_STAGED_WITH_EMBEDDING_SQL = """
SELECT fact_id, embedding
FROM staged_facts
WHERE status = 'staged'
  AND embedding IS NOT NULL
"""

_INCREMENT_REINFORCEMENT_SQL = (
    "UPDATE staged_facts SET reinforcement_count = reinforcement_count + 1 "
    "WHERE fact_id = ?"
)


class FactReinforcer:
    """Reinforces :class:`StagedFact` items similar to a new conversation.

    For every staged fact with an embedding, computes cosine similarity to
    the embedded conversation summary. Facts whose similarity exceeds the
    configured threshold (default ``0.75``) get their
    ``reinforcement_count`` bumped by one.
    """

    def __init__(
        self,
        db: DbPool,
        embedding_registry: EmbeddingRegistry | None = None,
        similarity_threshold: float = 0.75,
    ) -> None:
        # 1. ENTRY
        log.memory.debug(
            "[memory] fact_reinforcer.init: entry",
            extra={
                "_fields": {
                    "has_embeddings": embedding_registry is not None,
                    "similarity_threshold": similarity_threshold,
                }
            },
        )
        self._db = db
        self._embeddings = embedding_registry
        self._similarity_threshold = similarity_threshold
        # 4. EXIT
        log.memory.debug("[memory] fact_reinforcer.init: exit")

    async def reinforce_from_conversation(
        self, conversation_id: str, conversation_summary: str
    ) -> int:
        """Reinforce staged facts similar to ``conversation_summary``."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] fact_reinforcer.reinforce: entry",
            extra={
                "_fields": {
                    "conversation_id": conversation_id,
                    "summary_len": len(conversation_summary),
                }
            },
        )

        # 2. DECISION — degrade gracefully without embeddings
        if self._embeddings is None:
            log.memory.warning(
                "[memory] fact_reinforcer.reinforce: no embedding registry — returning 0",
                extra={"_fields": {"conversation_id": conversation_id}},
            )
            return 0

        # 3. STEP — embed the conversation summary
        try:
            provider = self._embeddings.get()
            conv_embeddings = await provider.embed([conversation_summary])
        except Exception as exc:
            # B5
            log.memory.warning(
                "[memory] fact_reinforcer.reinforce: embedding failed",
                exc_info=exc,
                extra={"_fields": {"conversation_id": conversation_id}},
            )
            return 0

        if not conv_embeddings:
            log.memory.warning(
                "[memory] fact_reinforcer.reinforce: provider returned no embedding",
                extra={"_fields": {"conversation_id": conversation_id}},
            )
            return 0
        conv_vec = np.array(conv_embeddings[0], dtype=np.float32)

        # 3. STEP — fetch staged facts that have embeddings
        rows = await self._db.fetch_all(_SELECT_STAGED_WITH_EMBEDDING_SQL)
        log.memory.debug(
            "[memory] fact_reinforcer.reinforce: candidates fetched",
            extra={
                "_fields": {
                    "conversation_id": conversation_id,
                    "candidate_count": len(rows),
                }
            },
        )

        reinforced = 0
        for row in rows:
            try:
                fact_id = row["fact_id"]
                fact_embedding = unpack_embedding(row.get("embedding"))
                if not fact_embedding:
                    continue
                sim = _cosine_similarity(conv_vec, fact_embedding)
                if sim > self._similarity_threshold:
                    await self._db.execute(
                        _INCREMENT_REINFORCEMENT_SQL, (fact_id,)
                    )
                    reinforced += 1
                    log.memory.info(
                        "[memory] fact_reinforcer: reinforced",
                        extra={
                            "_fields": {
                                "fact_id": fact_id,
                                "similarity": sim,
                                "conversation_id": conversation_id,
                            }
                        },
                    )
            except Exception as exc:
                # B5
                log.memory.warning(
                    "[memory] fact_reinforcer.reinforce: row failed — skipping",
                    exc_info=exc,
                    extra={"_fields": {"fact_id": row.get("fact_id")}},
                )

        # 4. EXIT
        log.memory.info(
            "[memory] fact_reinforcer.reinforce: exit",
            extra={
                "_fields": {
                    "conversation_id": conversation_id,
                    "reinforced_count": reinforced,
                    "candidates": len(rows),
                }
            },
        )
        return reinforced


def _cosine_similarity(a: np.ndarray, fact_embedding: list[float]) -> float:
    """Pure numpy cosine similarity. Returns 0.0 if either norm is zero."""
    b = np.array(fact_embedding, dtype=np.float32)
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b + 1e-9))
