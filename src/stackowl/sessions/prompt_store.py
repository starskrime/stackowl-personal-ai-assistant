"""Persistence for the per-session system prompt (D01.1).

Built once when a conversation starts, reused verbatim for every turn of it, and
discarded when the session rolls over. See ``docs/hermes-mapping/designs/D01.1.md``
for the design and the divergences from the reference platform.

The row is keyed ``(session_key, owl_name)`` and STAMPED with the incarnation it
was built for. That stamp is what makes invariant I6 self-enforcing: after a
rollover mints a new ``session_id`` the stored prompt no longer matches, so the
next turn cold-builds — no invalidation job, no listener, no way to forget.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.infra.prompt_metrics import digest


@dataclass(frozen=True, slots=True)
class StoredPrompt:
    """A frozen system prompt and the incarnation it belongs to."""

    session_key: str
    owl_name: str
    session_id: str
    prompt_text: str
    prompt_hash: str
    model_window: int | None
    built_at: str


class SessionPromptStore:
    """Owns session-prompt persistence. One instance, injected — never a global."""

    def __init__(self, db: DbPool) -> None:
        self._db = db

    async def load(
        self, *, session_key: str, owl_name: str, session_id: str
    ) -> StoredPrompt | None:
        """The frozen prompt for THIS incarnation, or ``None`` to cold-build.

        Returns ``None`` when the stored row was built for a different
        ``session_id`` — the rollover case — rather than deleting it, so the
        mismatch is a read-time rule and a still-live older incarnation can read
        its own prompt.

        Never raises: a store that cannot answer must cost a rebuild, not the
        turn (invariant I2).
        """
        log.gateway.debug(
            "[prompt] store.load: entry",
            extra={"_fields": {"session_key": session_key, "owl": owl_name,
                               "session_id": session_id}},
        )
        try:
            rows = await self._db.fetch_all(
                """
                SELECT session_key, owl_name, session_id, prompt_text,
                       prompt_hash, model_window, built_at
                FROM session_prompts
                WHERE session_key = ? AND owl_name = ? AND session_id = ?
                """,
                (session_key, owl_name, session_id),
            )
        except Exception as exc:
            log.gateway.error(
                "[prompt] store.load: read failed — the turn cold-builds instead",
                exc_info=exc,
                extra={"_fields": {"session_key": session_key, "owl": owl_name}},
            )
            return None
        if not rows:
            log.gateway.debug(
                "[prompt] store.load: exit — no prompt for this incarnation",
                extra={"_fields": {"session_key": session_key, "owl": owl_name}},
            )
            return None
        row = rows[0]
        window = row["model_window"]
        found = StoredPrompt(
            session_key=str(row["session_key"]),
            owl_name=str(row["owl_name"]),
            session_id=str(row["session_id"]),
            prompt_text=str(row["prompt_text"]),
            prompt_hash=str(row["prompt_hash"]),
            model_window=int(window) if window is not None else None,
            built_at=str(row["built_at"]),
        )
        log.gateway.debug(
            "[prompt] store.load: exit — hit",
            extra={"_fields": {"session_key": session_key, "owl": owl_name,
                               "prompt_hash": found.prompt_hash,
                               "prompt_chars": len(found.prompt_text)}},
        )
        return found

    async def save(
        self,
        *,
        session_key: str,
        owl_name: str,
        session_id: str,
        prompt_text: str,
        model_window: int | None,
        now: datetime.datetime | None = None,
    ) -> None:
        """Freeze this prompt for the life of the incarnation.

        An EMPTY prompt is not persisted. A cold build that produced nothing is a
        failure to assemble, not a prompt worth freezing — storing it would pin
        that failure for the whole session, and the next turn deserves another
        attempt.

        The hash is derived from the text here rather than accepted from the
        caller: it is what invariant I1 is measured with, so it must not be
        possible for the two to disagree.

        Never raises: failing to cache must cost a rebuild, not the turn.
        """
        if not prompt_text:
            log.gateway.warning(
                "[prompt] store.save: refusing to freeze an EMPTY prompt — "
                "the next turn will build again",
                extra={"_fields": {"session_key": session_key, "owl": owl_name,
                                   "session_id": session_id}},
            )
            return
        stamp = (now or datetime.datetime.now(datetime.UTC)).isoformat()
        prompt_hash = digest(prompt_text)
        try:
            # ON CONFLICT on the (session_key, owl_name) key, so a rebuild after a
            # boundary REPLACES rather than accumulating one row per incarnation.
            await self._db.execute(
                """
                INSERT INTO session_prompts (
                    session_key, owl_name, session_id, prompt_text,
                    prompt_hash, model_window, built_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key, owl_name) DO UPDATE SET
                    session_id   = excluded.session_id,
                    prompt_text  = excluded.prompt_text,
                    prompt_hash  = excluded.prompt_hash,
                    model_window = excluded.model_window,
                    built_at     = excluded.built_at
                """,
                (session_key, owl_name, session_id, prompt_text,
                 prompt_hash, model_window, stamp),
            )
        except Exception as exc:
            log.gateway.error(
                "[prompt] store.save: write failed — the next turn rebuilds",
                exc_info=exc,
                extra={"_fields": {"session_key": session_key, "owl": owl_name}},
            )
            return
        log.gateway.info(
            "[prompt] store.save: exit — prompt frozen for this session",
            extra={"_fields": {"session_key": session_key, "owl": owl_name,
                               "session_id": session_id,
                               "prompt_hash": prompt_hash,
                               "prompt_chars": len(prompt_text),
                               "model_window": model_window}},
        )
