"""FactExtractionJobHandler — scheduled fact extraction after N session messages."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, ClassVar

from stackowl.exceptions import DuplicateFactError
from stackowl.infra.observability import log
from stackowl.providers.base import Message
from stackowl.scheduler.base import JobHandler, TriggerKind
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.db.pool import DbPool
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.memory.fact_extractor import FactExtractor


_IDEMPOTENCY_PREFIX = "fact_extraction:"

_FETCH_SESSION_MESSAGES_SQL = """
SELECT m.role, m.content
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
WHERE c.session_id = ?
ORDER BY m.created_at DESC
LIMIT ?
"""


class FactExtractionJobHandler(JobHandler):
    """Runs fact extraction after N messages in a session.

    The job's session identifier is carried in
    ``Job.idempotency_key`` using the convention
    ``"fact_extraction:<session_id>"``. The handler fetches the most recent
    ``limit`` messages for that session from the ``messages`` table, runs
    :class:`FactExtractor`, then stages each fact via the
    :class:`MemoryBridge`.
    """

    _handler_name: ClassVar[str] = "fact_extraction"

    def __init__(
        self,
        extractor: FactExtractor,
        memory_bridge: MemoryBridge,
        db: DbPool,
        message_limit: int = 20,
    ) -> None:
        # 1. ENTRY
        log.memory.debug(
            "[memory] fact_extraction_handler.init: entry",
            extra={"_fields": {"message_limit": message_limit}},
        )
        self._extractor = extractor
        self._memory_bridge = memory_bridge
        self._db = db
        self._message_limit = message_limit
        # 4. EXIT
        log.memory.debug("[memory] fact_extraction_handler.init: exit")

    @property
    def handler_name(self) -> str:
        return self._handler_name

    @property
    def trigger_kind(self) -> TriggerKind:
        # Enqueued per-session ON DEMAND (idempotency key fact_extraction:<sid>)
        # after N messages — there is NO standing SchedulerAssembly seed. Declares
        # on_demand so the wiring audit does not flag the absence of a row.
        return "on_demand"

    async def execute(self, job: Job) -> JobResult:
        """Extract facts from session messages and stage them."""
        # 1. ENTRY
        log.memory.info(
            "[memory] fact_extraction_handler.execute: entry",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "idempotency_key": job.idempotency_key,
                }
            },
        )
        t0 = time.monotonic()

        # 2. DECISION — parse session_id from idempotency_key
        session_id = self._parse_session_id(job.idempotency_key)
        if session_id is None:
            duration_ms = (time.monotonic() - t0) * 1000.0
            log.memory.warning(
                "[memory] fact_extraction_handler.execute: invalid idempotency_key",
                extra={
                    "_fields": {
                        "job_id": job.job_id,
                        "idempotency_key": job.idempotency_key,
                    }
                },
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change",
                success=False,
                output=None,
                error=f"idempotency_key must start with {_IDEMPOTENCY_PREFIX!r}",
                duration_ms=duration_ms,
            )

        # 3. STEP — fetch messages for the session
        try:
            messages = await self._fetch_messages(session_id)
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000.0
            # B5
            log.memory.error(
                "[memory] fact_extraction_handler.execute: fetch failed",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "session_id": session_id}},
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change",
                success=False,
                output=None,
                error=f"fetch_messages failed: {exc}",
                duration_ms=duration_ms,
            )

        if not messages:
            duration_ms = (time.monotonic() - t0) * 1000.0
            log.memory.info(
                "[memory] fact_extraction_handler.execute: no messages — skipping",
                extra={"_fields": {"job_id": job.job_id, "session_id": session_id}},
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change",
                success=True,
                output="no_messages",
                error=None,
                duration_ms=duration_ms,
            )

        # 3. STEP — extract facts
        try:
            facts = await self._extractor.extract(messages, session_id)
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000.0
            # B5
            log.memory.error(
                "[memory] fact_extraction_handler.execute: extraction failed",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "session_id": session_id}},
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change",
                success=False,
                output=None,
                error=f"extract failed: {exc}",
                duration_ms=duration_ms,
            )

        # 3. STEP — stage each fact
        staged_count = 0
        for fact in facts:
            try:
                await self._memory_bridge.stage(fact)
                staged_count += 1
            except DuplicateFactError as exc:
                # B5 — duplicates are expected (e.g., re-runs); log and continue
                log.memory.warning(
                    "[memory] fact_extraction_handler.execute: duplicate fact — skipping",
                    exc_info=exc,
                    extra={"_fields": {"fact_id": fact.fact_id}},
                )
            except Exception as exc:
                # B5
                log.memory.warning(
                    "[memory] fact_extraction_handler.execute: stage failed — skipping",
                    exc_info=exc,
                    extra={"_fields": {"fact_id": fact.fact_id}},
                )

        duration_ms = (time.monotonic() - t0) * 1000.0
        # 4. EXIT
        log.memory.info(
            "[memory] fact_extraction_handler.execute: exit",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "session_id": session_id,
                    "extracted": len(facts),
                    "staged": staged_count,
                    "duration_ms": duration_ms,
                }
            },
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change",
            success=True,
            output=f"extracted={len(facts)} staged={staged_count}",
            error=None,
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------ helpers

    def _parse_session_id(self, idempotency_key: str) -> str | None:
        if not idempotency_key.startswith(_IDEMPOTENCY_PREFIX):
            return None
        session_id = idempotency_key[len(_IDEMPOTENCY_PREFIX):]
        return session_id or None

    async def _fetch_messages(self, session_id: str) -> list[Message]:
        rows = await self._db.fetch_all(
            _FETCH_SESSION_MESSAGES_SQL, (session_id, self._message_limit)
        )
        # Reverse so oldest is first (DB returned DESC for limit purposes)
        rows = list(reversed(rows))
        out: list[Message] = []
        for row in rows:
            role = row["role"]
            if role not in ("system", "user", "assistant", "tool"):
                log.memory.warning(
                    "[memory] fact_extraction_handler._fetch_messages: unknown role — skipping",
                    extra={"_fields": {"role": role}},
                )
                continue
            out.append(Message(role=role, content=row["content"]))
        return out
