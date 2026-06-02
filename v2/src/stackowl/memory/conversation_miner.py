"""ConversationMiner — extract long-term facts from staged conversation turns.

RC-A fix: conversation turns are persisted to staged_facts(source_type='conversation')
but recall() reads only committed_facts. This miner (run by the DreamWorker) extracts
durable facts and stages them (source_type='conversation_fact') so the promotion step
can commit them. Idempotent: re-mining the same turns does not duplicate facts.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from stackowl.exceptions import DuplicateFactError
from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.memory.fact_extractor import EXTRACTED_FACT_SOURCE_TYPE
from stackowl.memory.sqlite_helpers import cosine_similarity, unpack_embedding
from stackowl.providers.base import Message

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.db.pool import DbPool
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.memory.fact_extractor import FactExtractor
    from stackowl.memory.models import StagedFact

_DISTINCT_SESSIONS_SQL = (
    "SELECT DISTINCT source_ref FROM staged_facts WHERE source_type = 'conversation'"
)
_EXISTS_STAGED_SQL = (
    "SELECT 1 FROM staged_facts WHERE source_type = ? AND source_ref = ? AND content = ? LIMIT 1"
)
_EXISTS_COMMITTED_SQL = (
    "SELECT 1 FROM committed_facts WHERE source_ref = ? AND content = ? LIMIT 1"
)
_REINFORCE_SQL = (
    "UPDATE staged_facts SET reinforcement_count = reinforcement_count + 1 "
    "WHERE source_type = ? AND source_ref = ? AND content = ? AND status = 'staged'"
)
_REINFORCE_BY_ID_SQL = (
    "UPDATE staged_facts SET reinforcement_count = reinforcement_count + 1 "
    "WHERE fact_id = ? AND status = 'staged'"
)
# Staged conversation_facts for one source_ref — the candidate pool for semantic dedup.
_STAGED_FOR_REF_SQL = (
    "SELECT fact_id, embedding FROM staged_facts "
    "WHERE source_type = ? AND source_ref = ? AND status = 'staged'"
)


def _parse_turns(contents: list[str]) -> list[Message]:
    msgs: list[Message] = []
    for content in contents:
        user_part, _, assistant_part = content.partition("\n\nAssistant:")
        user_text = user_part.removeprefix("User:").strip()
        assistant_text = assistant_part.strip()
        if user_text:
            msgs.append(Message(role="user", content=user_text))
        if assistant_text:
            msgs.append(Message(role="assistant", content=assistant_text))
    return msgs


class ConversationMiner:
    """Mines staged conversation turns into staged long-term facts (idempotent)."""

    def __init__(
        self,
        db: DbPool,
        extractor: FactExtractor,
        bridge: MemoryBridge,
        message_limit: int = 40,
        dedup_similarity: float = 0.92,
        clock: Clock | None = None,
        settle_minutes: int = 0,
    ) -> None:
        # 1. ENTRY
        log.memory.debug(
            "[memory] conversation_miner.init: entry",
            extra={
                "_fields": {
                    "message_limit": message_limit,
                    "dedup_similarity": dedup_similarity,
                    "settle_minutes": settle_minutes,
                }
            },
        )
        self._db = db
        self._extractor = extractor
        self._bridge = bridge
        self._message_limit = message_limit
        self._dedup_similarity = dedup_similarity
        # Injected time source (ARCH-99) so the settle window is deterministic.
        self._clock: Clock = clock or WallClock()
        self._settle_minutes = settle_minutes
        # 4. EXIT
        log.memory.debug("[memory] conversation_miner.init: exit")

    def _settle_cutoff(self) -> str:
        """ISO-8601 (offset form) cutoff for turns eligible to mine."""
        return (
            self._clock.now() - timedelta(minutes=self._settle_minutes)
        ).isoformat()

    async def mine_all(self) -> int:
        """Mine all sessions with staged conversation turns. Returns total facts staged."""
        # 1. ENTRY
        log.memory.info("[memory] conversation_miner.mine_all: entry")
        rows = await self._db.fetch_all(_DISTINCT_SESSIONS_SQL)
        total = 0
        failed = 0
        for row in rows:
            session_id = row["source_ref"]
            try:
                total += await self.mine_session(session_id)
            except Exception as exc:  # B5
                failed += 1
                log.memory.error(
                    "[memory] conversation_miner.mine_all: session failed — skipping",
                    exc_info=exc,
                    extra={"_fields": {"session_id": session_id}},
                )
        # 4. EXIT
        if failed > 0:
            log.memory.error(
                "[memory] conversation_miner.mine_all: completed with failures",
                extra={"_fields": {"sessions": len(rows), "staged": total, "failed": failed}},
            )
        else:
            log.memory.info(
                "[memory] conversation_miner.mine_all: exit",
                extra={"_fields": {"sessions": len(rows), "staged": total, "failed": 0}},
            )
        return total

    async def mine_session(self, session_id: str) -> int:
        """Mine one session. Returns count of NEWLY staged facts (0 on re-mine; reinforcements not counted)."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] conversation_miner.mine_session: entry",
            extra={"_fields": {"session_id": session_id, "message_limit": self._message_limit}},
        )
        turns = await self._bridge.recent_conversation_turns(
            session_id=session_id,
            limit=self._message_limit,
            staged_before=self._settle_cutoff(),
        )
        # 2. DECISION
        if not turns:
            log.memory.debug(
                "[memory] conversation_miner.mine_session: no turns — exit early",
                extra={"_fields": {"session_id": session_id}},
            )
            return 0
        messages = _parse_turns([t.content for t in turns])
        if not messages:
            log.memory.debug(
                "[memory] conversation_miner.mine_session: no parseable messages — exit early",
                extra={"_fields": {"session_id": session_id}},
            )
            return 0
        # 3. STEP — extract facts via injected extractor
        facts = await self._extractor.extract(messages, session_id)
        log.memory.debug(
            "[memory] conversation_miner.mine_session: extracted facts",
            extra={"_fields": {"session_id": session_id, "fact_count": len(facts)}},
        )
        staged = 0
        reinforced = 0
        for fact in facts:
            committed = await self._db.fetch_all(_EXISTS_COMMITTED_SQL, (fact.source_ref, fact.content))
            if committed:
                # Already in committed_facts — no action needed.
                log.memory.debug(
                    "[memory] conversation_miner.mine_session: fact already committed — skip",
                    extra={"_fields": {"session_id": session_id}},
                )
                continue
            # Semantic dedup: reinforce an existing staged fact whose embedding is
            # near-identical (handles reworded re-extractions). Falls back to exact
            # content match when this fact has no embedding. Per-fact try/except so a
            # single malformed candidate never aborts the session (self-heal).
            try:
                reinforced_existing = await self._reinforce_if_duplicate(fact, session_id)
            except Exception as exc:  # B5 — never silently drop; degrade to staging.
                log.memory.error(
                    "[memory] conversation_miner.mine_session: dedup check FAILED — staging anyway",
                    exc_info=exc,
                    extra={"_fields": {"session_id": session_id, "fact_id": fact.fact_id}},
                )
                reinforced_existing = False
            if reinforced_existing:
                reinforced += 1
                log.memory.debug(
                    "[memory] conversation_miner.mine_session: fact reinforced",
                    extra={"_fields": {"session_id": session_id, "reinforced": reinforced}},
                )
                continue
            try:
                await self._bridge.stage(fact)
                staged += 1
            except DuplicateFactError as exc:  # Expected idempotency — quiet warning.
                log.memory.warning(
                    "[memory] conversation_miner: duplicate fact_id — skipping",
                    exc_info=exc,
                    extra={"_fields": {"fact_id": fact.fact_id}},
                )
            except Exception as exc:  # Unexpected stage error — loud, skip this fact only.
                log.memory.error(
                    "[memory] conversation_miner: stage FAILED — skipping fact",
                    exc_info=exc,
                    extra={"_fields": {"session_id": session_id, "fact_id": fact.fact_id}},
                )
        # 4. EXIT
        log.memory.info(
            "[memory] conversation_miner.mine_session: exit",
            extra={"_fields": {"session_id": session_id, "staged": staged, "reinforced": reinforced}},
        )
        return staged

    async def _reinforce_if_duplicate(self, fact: StagedFact, session_id: str) -> bool:
        """Reinforce an existing staged conversation_fact this fact duplicates.

        Strategy:
          1. SEMANTIC — if ``fact`` has an embedding, compare it (cosine) against
             every staged conversation_fact for the same ``source_ref``. If the
             best match >= the configured threshold, bump that row by ``fact_id``.
          2. FALLBACK (exact) — when ``fact`` has no embedding (or no embedded
             candidate cleared the bar), fall back to exact-content match so the
             miner never crashes and never silently drops a re-derivation.

        Returns ``True`` when an existing fact was reinforced (caller skips staging).
        """
        # 1. SEMANTIC path — only when this fact carries an embedding.
        if fact.embedding:
            candidates = await self._db.fetch_all(
                _STAGED_FOR_REF_SQL, (EXTRACTED_FACT_SOURCE_TYPE, fact.source_ref)
            )
            best_id: str | None = None
            best_sim = -1.0
            for cand in candidates:
                cand_emb = unpack_embedding(cand["embedding"]) or None
                sim = cosine_similarity(fact.embedding, cand_emb)
                if sim is None:
                    continue
                if sim > best_sim:
                    best_sim, best_id = sim, cand["fact_id"]
            if best_id is not None and best_sim >= self._dedup_similarity:
                await self._db.execute(_REINFORCE_BY_ID_SQL, (best_id,))
                log.memory.debug(
                    "[memory] conversation_miner.dedup: semantic match reinforced",
                    extra={
                        "_fields": {
                            "session_id": session_id,
                            "matched_fact_id": best_id,
                            "similarity": round(best_sim, 4),
                            "threshold": self._dedup_similarity,
                        }
                    },
                )
                return True
            # Embedded but nothing cleared the bar — let exact-match below have a
            # turn (covers a stored row that happens to lack an embedding).
            log.memory.debug(
                "[memory] conversation_miner.dedup: no semantic match — checking exact content",
                extra={
                    "_fields": {
                        "session_id": session_id,
                        "best_similarity": round(best_sim, 4) if best_id else None,
                        "threshold": self._dedup_similarity,
                    }
                },
            )
        else:
            # 2. FALLBACK trigger — embedding unavailable for this fact.
            log.memory.debug(
                "[memory] conversation_miner.dedup: no embedding — falling back to exact match",
                extra={"_fields": {"session_id": session_id, "fact_id": fact.fact_id}},
            )

        # Exact-content fallback (the original behaviour).
        already_staged = await self._db.fetch_all(
            _EXISTS_STAGED_SQL, (EXTRACTED_FACT_SOURCE_TYPE, fact.source_ref, fact.content)
        )
        if already_staged:
            await self._db.execute(
                _REINFORCE_SQL, (EXTRACTED_FACT_SOURCE_TYPE, fact.source_ref, fact.content)
            )
            return True
        return False
