"""SessionStore — durable lanes, plus a human-readable mirror.

SQLite is the SINGLE source of truth. The JSON file is a DERIVED, WRITE-ONLY
projection of the key->id map, refreshed after each commit and **never read back**
by the platform.

That asymmetry is the whole point. Bakir asked for both (Q4, reaffirmed with the
sync-bug cost on the table), and two stores that are both trusted is a bug class.
A one-way projection cannot drift into disagreement, because nothing consults it:
if the mirror is deleted, corrupted, or hand-edited, the next write regenerates it
and the platform never noticed. It buys legibility at no correctness cost.

Resolution itself lives in :mod:`stackowl.sessions.policy` as pure functions. This
module only persists what that decided — so the branch logic stays testable without
a database, and this stays testable without re-deriving policy.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.paths import StackowlHome
from stackowl.sessions.models import (
    Branch,
    ResetReason,
    SessionEntry,
    SessionSource,
    build_session_key,
    new_session_id,
)
from stackowl.sessions.policy import ResetPolicy, resolve

_MIRROR_NAME = "sessions.json"

_COLUMNS = (
    "session_key, session_id, owl_name, channel, created_at, updated_at, "
    "turn_count, suspended, resume_pending, resume_reason, was_auto_reset, "
    "auto_reset_reason, is_fresh_reset, expiry_finalized, restart_failures, "
    "chat_id"
)


def _to_entry(row: dict[str, Any]) -> SessionEntry:
    """Rehydrate an entry. Booleans are INTEGER in SQLite; reasons are strings."""
    reason = row.get("auto_reset_reason")
    return SessionEntry(
        session_key=row["session_key"],
        session_id=row["session_id"],
        owl_name=row["owl_name"],
        channel=row["channel"],
        created_at=datetime.datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.datetime.fromisoformat(row["updated_at"]),
        turn_count=int(row["turn_count"] or 0),
        suspended=bool(row["suspended"]),
        resume_pending=bool(row["resume_pending"]),
        resume_reason=row.get("resume_reason"),
        was_auto_reset=bool(row["was_auto_reset"]),
        auto_reset_reason=ResetReason(reason) if reason else None,
        is_fresh_reset=bool(row["is_fresh_reset"]),
        expiry_finalized=bool(row["expiry_finalized"]),
        restart_failures=int(row["restart_failures"] or 0),
        chat_id=row.get("chat_id"),
    )


class SessionStore:
    """Owns lane persistence. One instance, injected — never a module global."""

    def __init__(self, db: DbPool, policy: ResetPolicy | None = None,
                 mirror_dir: Path | None = None) -> None:
        self._db = db
        self._policy = policy or ResetPolicy()
        self._mirror_dir = mirror_dir

    # ------------------------------------------------------------------ read

    async def get(self, session_key: str) -> SessionEntry | None:
        rows = await self._db.fetch_all(
            f"SELECT {_COLUMNS} FROM sessions WHERE session_key = ?", (session_key,)
        )
        return _to_entry(rows[0]) if rows else None

    async def resolve_send_target(self, session_key: str) -> str | None:
        """The channel-native destination for ``session_key``, or ``None``.

        This is what proactive delivery needs and what a composite lane key can no
        longer supply on its own. ``None`` for an unknown lane or a channel with no
        per-lane destination — the caller then falls back loudly. A fabricated
        recipient IS the cross-delivery bug, so this never guesses.
        """
        entry = await self.get(session_key)
        target = entry.chat_id if entry else None
        log.gateway.debug(
            "session.resolve_send_target: exit",
            extra={"_fields": {"session_key": session_key, "known_lane": entry is not None,
                               "resolved": target is not None}},
        )
        return target

    async def list_all(self) -> list[SessionEntry]:
        rows = await self._db.fetch_all(
            f"SELECT {_COLUMNS} FROM sessions ORDER BY updated_at DESC"
        )
        return [_to_entry(r) for r in rows]

    # ----------------------------------------------------------- resolution

    async def resolve_for(
        self, source: SessionSource, now: datetime.datetime,
        *, has_active_work: bool = False,
        group_per_user: bool = True, thread_per_user: bool = False,
    ) -> tuple[SessionEntry, Branch, ResetReason | None]:
        """Resolve the lane for an inbound message and persist the outcome.

        Returns ``(entry, branch, reason)``. The branch is returned rather than
        inferred by the caller so the DECISION point is logged from one place and
        a caller can never disagree with what actually happened.
        """
        key = build_session_key(source, group_per_user=group_per_user,
                                thread_per_user=thread_per_user)
        log.gateway.debug(
            "session.resolve: entry",
            extra={"_fields": {"owl": source.owl_name, "channel": source.channel,
                               "chat_type": source.chat_type.value,
                               "session_key": key}},
        )
        existing = await self.get(key)
        decision = resolve(existing, now, self._policy, has_active_work=has_active_work)

        # The newest message wins on the send target — a Telegram group upgraded to
        # a supergroup re-keys the chat while staying the SAME lane — but a message
        # that carries no target must not erase one we already have.
        target = source.chat_target or (existing.chat_id if existing else None)

        if existing is None:
            entry = SessionEntry(
                session_key=key, session_id=new_session_id(now),
                owl_name=source.owl_name, channel=source.channel,
                created_at=now, updated_at=now, chat_id=target,
            )
        elif decision.mints_new_incarnation:
            # A rollover ENDS an incarnation; it never destroys a transcript
            # (invariant I6). The old session_id stays referenced by messages/
            # cost_records, so the conversation remains searchable.
            entry = existing.evolve(
                session_id=new_session_id(now),
                created_at=now, updated_at=now, turn_count=0,
                suspended=False, resume_pending=False, resume_reason=None,
                was_auto_reset=decision.reason is not None
                and decision.reason.is_automatic,
                auto_reset_reason=decision.reason,
                is_fresh_reset=False, expiry_finalized=False,
                restart_failures=0, chat_id=target,
            )
        else:
            entry = existing.evolve(updated_at=now, chat_id=target)

        await self.save(entry)
        log.gateway.info(
            "session.resolve: branch taken",
            extra={"_fields": {"session_key": key, "branch": decision.branch.value,
                               "reason": decision.reason.value if decision.reason else None,
                               "session_id": entry.session_id,
                               "previous_session_id": existing.session_id if existing else None}},
        )
        if decision.mints_new_incarnation and existing is not None:
            log.gateway.info(
                "session.rollover: old incarnation ended",
                extra={"_fields": {
                    "session_key": key, "old_session_id": existing.session_id,
                    "new_session_id": entry.session_id,
                    "reason": decision.reason.value if decision.reason else None,
                    "turn_count": existing.turn_count,
                }},
            )
        return entry, decision.branch, decision.reason

    # ----------------------------------------------------------------- write

    async def save(self, entry: SessionEntry) -> SessionEntry:
        """Upsert a lane, then refresh the mirror. The mirror never blocks a save."""
        await self._db.execute(
            f"""
            INSERT INTO sessions ({_COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_key) DO UPDATE SET
                session_id=excluded.session_id, owl_name=excluded.owl_name,
                channel=excluded.channel, created_at=excluded.created_at,
                updated_at=excluded.updated_at, turn_count=excluded.turn_count,
                suspended=excluded.suspended, resume_pending=excluded.resume_pending,
                resume_reason=excluded.resume_reason,
                was_auto_reset=excluded.was_auto_reset,
                auto_reset_reason=excluded.auto_reset_reason,
                is_fresh_reset=excluded.is_fresh_reset,
                expiry_finalized=excluded.expiry_finalized,
                restart_failures=excluded.restart_failures,
                -- COALESCE, not excluded.chat_id: a channel that cannot state a
                -- target (CLI) must never blank a real one we already know, or a
                -- single CLI turn would silently unaddress the Telegram lane.
                chat_id=COALESCE(excluded.chat_id, sessions.chat_id)
            """,
            (
                entry.session_key, entry.session_id, entry.owl_name, entry.channel,
                entry.created_at.isoformat(), entry.updated_at.isoformat(),
                entry.turn_count, int(entry.suspended), int(entry.resume_pending),
                entry.resume_reason, int(entry.was_auto_reset),
                entry.auto_reset_reason.value if entry.auto_reset_reason else None,
                int(entry.is_fresh_reset), int(entry.expiry_finalized),
                entry.restart_failures, entry.chat_id,
            ),
        )
        await self._project_mirror()
        return entry

    async def consume_reset_notice(self, entry: SessionEntry) -> SessionEntry:
        """Clear ``was_auto_reset`` after the notice has been shown ONCE (I5)."""
        if not entry.was_auto_reset:
            return entry
        return await self.save(entry.evolve(was_auto_reset=False))

    async def clear_resume_pending(self, session_key: str) -> None:
        """Called after a resumed turn completes successfully."""
        entry = await self.get(session_key)
        if entry is not None and entry.resume_pending:
            await self.save(entry.evolve(resume_pending=False, resume_reason=None,
                                         restart_failures=0))

    async def prune(self, older_than: datetime.datetime) -> int:
        """Drop lane RECORDS not touched since ``older_than``. Transcripts stay.

        Bakir's Q11: keep the transcript, prune the record. A lane with active
        recovery state is never pruned — dropping it would lose the very fact that
        a turn needs resuming.
        """
        rows = await self._db.fetch_all(
            "SELECT session_key FROM sessions WHERE updated_at < ? "
            "AND suspended = 0 AND resume_pending = 0",
            (older_than.isoformat(),),
        )
        for row in rows:
            await self._db.execute("DELETE FROM sessions WHERE session_key = ?",
                                   (row["session_key"],))
        if rows:
            await self._project_mirror()
        log.gateway.info("session.prune: exit",
                         extra={"_fields": {"pruned": len(rows)}})
        return len(rows)

    # ---------------------------------------------------------------- mirror

    def mirror_path(self) -> Path:
        base = self._mirror_dir or StackowlHome.workspace()
        return base / _MIRROR_NAME

    async def _project_mirror(self) -> None:
        """Rewrite the key->id mirror. Failure is logged and swallowed.

        Nothing reads this file, so a failed projection cannot corrupt behaviour —
        at worst the human-readable view is stale until the next write. Letting it
        raise would make a debugging aid capable of breaking a conversation, which
        would be exactly backwards.
        """
        try:
            entries = await self.list_all()
            payload = {
                "_note": "DERIVED, WRITE-ONLY. SQLite is the source of truth; "
                         "nothing reads this file. Safe to delete — it regenerates.",
                "lanes": {
                    e.session_key: {
                        "session_id": e.session_id,
                        "owl": e.owl_name,
                        "channel": e.channel,
                        "created_at": e.created_at.isoformat(),
                        "updated_at": e.updated_at.isoformat(),
                        "turns": e.turn_count,
                    }
                    for e in entries
                },
            }
            path = self.mirror_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(path)  # atomic on POSIX and Windows
        except Exception as exc:  # no-hidden-errors: log, never break a turn
            log.gateway.error(
                "session.mirror: projection failed — SQLite is unaffected",
                exc_info=exc, extra={"_fields": {"path": str(self.mirror_path())}},
            )
