"""Persistence for the per-session system prompt (D01.1).

Built once when a conversation starts, reused verbatim for every turn of it, and
discarded when the session rolls over. See ``docs/reference-mapping/designs/D01.1.md``
for the design and the divergences from the reference platform.

The row is keyed ``(session_key, owl_name)`` and STAMPED with the incarnation it
was built for. That stamp is what makes invariant I6 self-enforcing: after a
rollover mints a new ``conversation_id`` the stored prompt no longer matches, so the
next turn cold-builds — no invalidation job, no listener, no way to forget.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.infra.prompt_invalidation import note_expected_change
from stackowl.infra.prompt_metrics import digest


@dataclass(frozen=True, slots=True)
class StoredPrompt:
    """A frozen system prompt and the incarnation it belongs to."""

    session_key: str
    owl_name: str
    conversation_id: str
    prompt_text: str
    prompt_hash: str
    model_window: int | None
    built_at: str


class SessionPromptStore:
    """Owns session-prompt persistence. One instance, injected — never a global."""

    def __init__(self, db: DbPool) -> None:
        self._db = db

    async def load(
        self, *, session_key: str, owl_name: str, conversation_id: str
    ) -> StoredPrompt | None:
        """The frozen prompt for THIS incarnation, or ``None`` to cold-build.

        Returns ``None`` when the stored row was built for a different
        ``conversation_id`` — the rollover case — rather than deleting it, so the
        mismatch is a read-time rule and a still-live older incarnation can read
        its own prompt.

        Never raises: a store that cannot answer must cost a rebuild, not the
        turn (invariant I2).
        """
        log.gateway.debug(
            "[prompt] store.load: entry",
            extra={"_fields": {"session_key": session_key, "owl": owl_name,
                               "conversation_id": conversation_id}},
        )
        try:
            rows = await self._db.fetch_all(
                """
                SELECT session_key, owl_name, conversation_id, prompt_text,
                       prompt_hash, model_window, built_at
                FROM session_prompts
                WHERE session_key = ? AND owl_name = ? AND conversation_id = ?
                """,
                (session_key, owl_name, conversation_id),
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
            conversation_id=str(row["conversation_id"]),
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

    async def _invalidate(
        self, *, sql: str, params: tuple[str, ...], cause: str, scope: str, owl: str
    ) -> int:
        """Run one invalidating DELETE and report how many prompts it cleared.

        Shared by both public methods so the logging contract and the fail-open
        behaviour are written once. Never raises: the change that triggered this
        has already persisted, and losing the cache clear must cost a stale
        prompt until rollover, never the operation the user asked for (I3).
        """
        try:
            # execute() returns None; the pool already has a rowcount variant, so
            # the count reported in the log line is measured, not assumed.
            cleared = await self._db.execute_returning_rowcount(sql, params)
        except Exception as exc:
            log.gateway.error(
                "[prompt] invalidate: FAILED — the change will not apply until rollover",
                exc_info=exc,
                extra={"_fields": {"cause": cause, "scope": scope, "owl": owl}},
            )
            return 0
        rows = int(cleared or 0)
        # INFO, not debug, deliberately. This is the missing half of D01.2's
        # audit: that one names WHICH PART of the prompt changed, this names WHAT
        # CAUSED the rebuild. Together they answer "why did this cost me a cache
        # miss" from the JSONL alone. D01.6 learned what debug-level costs — the
        # diagnostics it needed appeared in 0 of 17403 live log lines.
        log.gateway.info(
            "[prompt] invalidate: exit",
            extra={"_fields": {"cause": cause, "scope": scope, "owl": owl, "rows": rows}},
        )
        return rows

    async def invalidate_owl(self, *, owl_name: str, cause: str) -> int:
        """Clear this owl's frozen prompt on EVERY lane. Returns rows cleared.

        Per-owl and not per-lane on purpose: you edited the OWL, not one
        conversation, so ``secretary`` on Telegram, on the CLI and on two incident
        lanes all rebuild. Anything narrower lets the same owl silently disagree
        with itself across channels.

        ``cause`` is not decoration — it travels into the log line and is what
        lets D01.2's part-audit distinguish a change the user ASKED for from a
        silent invalidator, which is the whole reason that audit exists.
        """
        # D01.4 — tell the audit this rebuild was asked for, so D01.2's part
        # audit reports it at info instead of warning about a change the user
        # deliberately made. Recorded BEFORE the delete so the explanation is
        # already in place if the very next turn races in behind it.
        note_expected_change(owl_name, cause=cause)
        return await self._invalidate(
            sql="DELETE FROM session_prompts WHERE owl_name = ?",
            params=(owl_name,), cause=cause, scope="owl", owl=owl_name,
        )

    async def invalidate_all(self, *, cause: str) -> int:
        """Clear EVERY frozen prompt. Returns rows cleared.

        For changes that are not owl-scoped — the skills catalogue, the
        capabilities block, global permissions. Those are machine-wide facts, so
        every prompt containing them genuinely is stale.

        Global across principals, deliberately: ``session_prompts`` has no
        ``owner_id`` (migration 0102, "NO owner_id, DELIBERATELY"), because the
        parent ``sessions`` table has none either. Inventing a scoping model here
        would contradict that decision for no benefit — the catalogue really is
        shared.

        A separate method rather than ``invalidate(owl_name=None)`` so the
        destructive case can never be reached by forgetting an argument.
        """
        return await self._invalidate(
            sql="DELETE FROM session_prompts", params=(), cause=cause,
            scope="all", owl="",
        )

    async def save(
        self,
        *,
        session_key: str,
        owl_name: str,
        conversation_id: str,
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
                                   "conversation_id": conversation_id}},
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
                    session_key, owl_name, conversation_id, prompt_text,
                    prompt_hash, model_window, built_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key, owl_name) DO UPDATE SET
                    conversation_id   = excluded.conversation_id,
                    prompt_text  = excluded.prompt_text,
                    prompt_hash  = excluded.prompt_hash,
                    model_window = excluded.model_window,
                    built_at     = excluded.built_at
                """,
                (session_key, owl_name, conversation_id, prompt_text,
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
                               "conversation_id": conversation_id,
                               "prompt_hash": prompt_hash,
                               "prompt_chars": len(prompt_text),
                               "model_window": model_window}},
        )
