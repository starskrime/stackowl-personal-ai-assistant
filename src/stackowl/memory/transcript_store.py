"""TranscriptStore — the conversation transcript, finally written down.

The ``conversations`` and ``messages`` tables have existed since migration 0002
and **nothing has ever written to them**. Six subsystems read them and have
therefore been operating on an empty result set:

* ``owls/evolution.py`` — fetches excerpts to evolve owl DNA
* ``memory/extraction_handler.py`` — extracted facts from a session's messages.
  DELETED in D01.7 part 5b: it duplicated ``memory/conversation_miner.py`` and
  nothing had ever enqueued it. The boundary now nudges the miner instead.
* ``tools/knowledge/session_search.py`` — searches past conversations
* ``tools/knowledge/transcripts.py`` — reads a transcript back
* ``tools/knowledge/session_access.py`` — resolves a session's owner, for authz
* ``tools/scheduling/cron_helpers.py`` — resolves the owl that owns a cron job

Owl DNA evolution and fact extraction — two headline features — have been
running on nothing. Found while wiring `D01.7`, whose invariant I6 ("no
transcript is destroyed by a rollover") was unfalsifiable until a transcript
existed to preserve.

MODEL. One ``conversations`` row per INCARNATION, whose ``id`` IS the
``session_id``. That is not a shortcut: an incarnation is exactly "one run of one
lane", which is what a conversation row was always trying to describe. It makes
the rollover boundary and the transcript boundary the same boundary by
construction, so they cannot drift apart.

SCOPE. User and assistant messages only (Bakir's choice). Tool payloads and
system prompts stay out of a table the knowledge tools search — they are large,
and they would drown the content those tools exist to find.
"""

from __future__ import annotations

import datetime
import uuid

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log

_ROLE_USER = "user"
_ROLE_ASSISTANT = "assistant"


class TranscriptStore:
    """Appends a turn to the durable conversation transcript."""

    def __init__(self, db: DbPool) -> None:
        self._db = db

    async def record_turn(
        self,
        *,
        session_key: str,
        session_id: str,
        owl_name: str,
        user_text: str,
        assistant_text: str | None,
        trace_id: str = "",
        model: str | None = None,
        now: datetime.datetime | None = None,
    ) -> int:
        """Append this turn. Returns the number of message rows written.

        ``assistant_text`` is ``None`` on a floored turn: the user utterance is
        still recorded, but a floored draft is never written, matching what
        persist_turn already does for staged facts. A transcript that contains
        "I did it" for a turn that did not is worse than a gap.
        """
        # 1. ENTRY
        log.memory.debug(
            "transcript.record_turn: entry",
            extra={"_fields": {"session_key": session_key, "session_id": session_id,
                               "owl": owl_name, "has_assistant": assistant_text is not None,
                               "user_len": len(user_text or "")}},
        )
        if not session_id:
            # No incarnation means the turn never passed through ingress (a
            # scheduler handler, a parliament round). Those are not conversations
            # and must not invent one.
            log.memory.debug(
                "transcript.record_turn: exit — no incarnation, not a conversation",
                extra={"_fields": {"session_key": session_key}},
            )
            return 0
        if not user_text:
            log.memory.debug(
                "transcript.record_turn: exit — nothing to record",
                extra={"_fields": {"session_id": session_id}},
            )
            return 0

        stamp = now or datetime.datetime.now(datetime.UTC)
        # 2. DECISION — floored turns record the user side only.
        rows = [(_ROLE_USER, user_text)]
        if assistant_text:
            rows.append((_ROLE_ASSISTANT, assistant_text))
        log.memory.debug(
            "transcript.record_turn: recording",
            extra={"_fields": {"session_id": session_id, "rows": len(rows),
                               "floored": assistant_text is None}},
        )

        # 3. STEP — the conversation row is keyed by the INCARNATION, so a rollover
        # starts a new transcript without touching the old one (invariant I6).
        await self._db.execute(
            """
            INSERT INTO conversations (id, session_key, owl_name, started_at, message_count)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                message_count = conversations.message_count + excluded.message_count
            """,
            (session_id, session_key, owl_name, stamp.isoformat(), len(rows)),
        )
        for role, content in rows:
            await self._db.execute(
                """
                INSERT INTO messages (id, conversation_id, role, content, model,
                                      created_at, trace_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), session_id, role, content, model,
                 stamp.isoformat(), trace_id),
            )

        # 4. EXIT
        log.memory.info(
            "transcript.record_turn: exit",
            extra={"_fields": {"session_key": session_key, "session_id": session_id,
                               "rows_written": len(rows)}},
        )
        return len(rows)
