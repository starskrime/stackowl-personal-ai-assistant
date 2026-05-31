"""ConversationMiner — extract long-term facts from staged conversation turns.

RC-A fix: conversation turns are persisted to staged_facts(source_type='conversation')
but recall() reads only committed_facts. This miner (run by the DreamWorker) extracts
durable facts and stages them (source_type='conversation_fact') so the promotion step
can commit them. Idempotent: re-mining the same turns does not duplicate facts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.exceptions import DuplicateFactError
from stackowl.infra.observability import log
from stackowl.memory.fact_extractor import EXTRACTED_FACT_SOURCE_TYPE
from stackowl.providers.base import Message

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.db.pool import DbPool
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.memory.fact_extractor import FactExtractor

_DISTINCT_SESSIONS_SQL = (
    "SELECT DISTINCT source_ref FROM staged_facts WHERE source_type = 'conversation'"
)
_EXISTS_STAGED_SQL = (
    "SELECT 1 FROM staged_facts WHERE source_type = ? AND source_ref = ? AND content = ? LIMIT 1"
)
_EXISTS_COMMITTED_SQL = (
    "SELECT 1 FROM committed_facts WHERE source_ref = ? AND content = ? LIMIT 1"
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
    ) -> None:
        # 1. ENTRY
        log.memory.debug(
            "[memory] conversation_miner.init: entry",
            extra={"_fields": {"message_limit": message_limit}},
        )
        self._db = db
        self._extractor = extractor
        self._bridge = bridge
        self._message_limit = message_limit
        # 4. EXIT
        log.memory.debug("[memory] conversation_miner.init: exit")

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
        """Mine one session. Returns count of newly staged facts (0 when idempotent)."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] conversation_miner.mine_session: entry",
            extra={"_fields": {"session_id": session_id, "message_limit": self._message_limit}},
        )
        turns = await self._bridge.recent_conversation_turns(
            session_id=session_id, limit=self._message_limit
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
        for fact in facts:
            if await self._already_present(fact.source_ref, fact.content):
                log.memory.debug(
                    "[memory] conversation_miner.mine_session: fact already present — skip",
                    extra={"_fields": {"session_id": session_id}},
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
            extra={"_fields": {"session_id": session_id, "staged": staged}},
        )
        return staged

    async def _already_present(self, source_ref: str, content: str) -> bool:
        """Return True if this exact content is already staged or committed for source_ref."""
        if await self._db.fetch_all(
            _EXISTS_STAGED_SQL, (EXTRACTED_FACT_SOURCE_TYPE, source_ref, content)
        ):
            return True
        if await self._db.fetch_all(_EXISTS_COMMITTED_SQL, (source_ref, content)):
            return True
        return False
