"""SessionStore — SQLite persistence for ParliamentSession records."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from stackowl.db.pool import DbPool
from stackowl.exceptions import InfrastructureError
from stackowl.infra.observability import log
from stackowl.parliament.models import ParliamentRound, ParliamentSession
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID, OwnedRepository

_INSERT_SQL = """
INSERT INTO parliament_sessions (
    session_id, topic, owl_names, rounds, synthesis, status,
    started_at, completed_at, interjections, owner_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_UPDATE_FINAL_SQL = """
UPDATE parliament_sessions
   SET status = ?, completed_at = ?, synthesis = ?, rounds = ?, interjections = ?
 WHERE session_id = ? AND owner_id = ?
"""

_UPDATE_ROUNDS_SQL = """
UPDATE parliament_sessions
   SET rounds = ?, interjections = ?
 WHERE session_id = ? AND owner_id = ?
"""

_SELECT_RECENT_SQL = """
SELECT session_id, topic, owl_names, rounds, synthesis, status,
       started_at, completed_at, interjections
  FROM parliament_sessions
 WHERE owner_id = ?
 ORDER BY started_at DESC
 LIMIT ?
"""

_SELECT_BY_ID_SQL = """
SELECT session_id, topic, owl_names, rounds, synthesis, status,
       started_at, completed_at, interjections
  FROM parliament_sessions
 WHERE owner_id = ? AND session_id = ?
"""


class SessionStore(OwnedRepository):
    """Persists ParliamentSession records to SQLite.

    All operations are 4-point logged. Serialisation is JSON for list/dict
    fields; datetimes are ISO 8601 strings. The store never raises bare
    Exception — DB faults surface as InfrastructureError. Owner-scoped: every
    read/write is constrained to ``owner_id`` (defaults to the single-user
    :data:`DEFAULT_PRINCIPAL_ID`, so existing behavior is unchanged).
    """

    _table = "parliament_sessions"

    def __init__(self, db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        super().__init__(db, owner_id)

    async def create(self, session: ParliamentSession) -> None:
        """Insert a new session row."""
        log.parliament.debug(
            "[parliament] session_store.create: entry",
            extra={"_fields": {"session_id": session.session_id, "topic_len": len(session.topic)}},
        )
        t0 = time.monotonic()
        params = self._serialize_full(session)
        try:
            await self._db.execute(_INSERT_SQL, params)
        except Exception as exc:
            log.parliament.warning(
                "[parliament] session_store.create: insert failed",
                exc_info=exc,
                extra={"_fields": {"session_id": session.session_id}},
            )
            raise InfrastructureError(f"session_store.create failed: {exc}") from exc
        log.parliament.debug(
            "[parliament] session_store.create: exit",
            extra={
                "_fields": {
                    "session_id": session.session_id,
                    "duration_ms": (time.monotonic() - t0) * 1000.0,
                }
            },
        )

    async def update_rounds(self, session: ParliamentSession) -> None:
        """Update only the rounds/interjections columns mid-session."""
        log.parliament.debug(
            "[parliament] session_store.update_rounds: entry",
            extra={
                "_fields": {
                    "session_id": session.session_id,
                    "rounds": len(session.rounds),
                    "interjections": len(session.interjections),
                }
            },
        )
        t0 = time.monotonic()
        params = (
            json.dumps([r.model_dump() for r in session.rounds]),
            json.dumps(session.interjections),
            session.session_id,
            self._owner_id,
        )
        try:
            await self._db.execute(_UPDATE_ROUNDS_SQL, params)
        except Exception as exc:
            log.parliament.warning(
                "[parliament] session_store.update_rounds: update failed",
                exc_info=exc,
                extra={"_fields": {"session_id": session.session_id}},
            )
            raise InfrastructureError(f"session_store.update_rounds failed: {exc}") from exc
        log.parliament.debug(
            "[parliament] session_store.update_rounds: exit",
            extra={
                "_fields": {
                    "session_id": session.session_id,
                    "duration_ms": (time.monotonic() - t0) * 1000.0,
                }
            },
        )

    async def update_final(self, session: ParliamentSession) -> None:
        """Persist terminal state (completed or failed) for a session."""
        log.parliament.debug(
            "[parliament] session_store.update_final: entry",
            extra={
                "_fields": {
                    "session_id": session.session_id,
                    "status": session.status,
                }
            },
        )
        t0 = time.monotonic()
        params = (
            session.status,
            session.completed_at.isoformat() if session.completed_at else None,
            session.synthesis,
            json.dumps([r.model_dump() for r in session.rounds]),
            json.dumps(session.interjections),
            session.session_id,
            self._owner_id,
        )
        try:
            await self._db.execute(_UPDATE_FINAL_SQL, params)
        except Exception as exc:
            log.parliament.warning(
                "[parliament] session_store.update_final: update failed",
                exc_info=exc,
                extra={"_fields": {"session_id": session.session_id}},
            )
            raise InfrastructureError(f"session_store.update_final failed: {exc}") from exc
        log.parliament.debug(
            "[parliament] session_store.update_final: exit",
            extra={
                "_fields": {
                    "session_id": session.session_id,
                    "duration_ms": (time.monotonic() - t0) * 1000.0,
                }
            },
        )

    async def list_recent(self, limit: int = 5) -> list[ParliamentSession]:
        """Return up to ``limit`` recent sessions, newest first."""
        log.parliament.debug(
            "[parliament] session_store.list_recent: entry",
            extra={"_fields": {"limit": limit}},
        )
        t0 = time.monotonic()
        try:
            rows = await self._db.fetch_all(_SELECT_RECENT_SQL, (self._owner_id, limit))
        except Exception as exc:
            log.parliament.warning(
                "[parliament] session_store.list_recent: query failed",
                exc_info=exc,
                extra={"_fields": {"limit": limit}},
            )
            raise InfrastructureError(f"session_store.list_recent failed: {exc}") from exc
        sessions = [self._row_to_session(row) for row in rows]
        log.parliament.debug(
            "[parliament] session_store.list_recent: exit",
            extra={
                "_fields": {
                    "count": len(sessions),
                    "duration_ms": (time.monotonic() - t0) * 1000.0,
                }
            },
        )
        return sessions

    async def get_by_id(self, session_id: str) -> ParliamentSession | None:
        """Fetch a single session by ID, or None if not present."""
        log.parliament.debug(
            "[parliament] session_store.get_by_id: entry",
            extra={"_fields": {"session_id": session_id}},
        )
        t0 = time.monotonic()
        try:
            rows = await self._db.fetch_all(_SELECT_BY_ID_SQL, (self._owner_id, session_id))
        except Exception as exc:
            log.parliament.warning(
                "[parliament] session_store.get_by_id: query failed",
                exc_info=exc,
                extra={"_fields": {"session_id": session_id}},
            )
            raise InfrastructureError(f"session_store.get_by_id failed: {exc}") from exc
        if not rows:
            log.parliament.debug(
                "[parliament] session_store.get_by_id: exit — not found",
                extra={"_fields": {"session_id": session_id}},
            )
            return None
        session = self._row_to_session(rows[0])
        log.parliament.debug(
            "[parliament] session_store.get_by_id: exit",
            extra={
                "_fields": {
                    "session_id": session_id,
                    "duration_ms": (time.monotonic() - t0) * 1000.0,
                }
            },
        )
        return session

    def _serialize_full(self, session: ParliamentSession) -> tuple[Any, ...]:
        return (
            session.session_id,
            session.topic,
            json.dumps(session.owl_names),
            json.dumps([r.model_dump() for r in session.rounds]),
            session.synthesis,
            session.status,
            session.started_at.isoformat(),
            session.completed_at.isoformat() if session.completed_at else None,
            json.dumps(session.interjections),
            self._owner_id,
        )

    def _row_to_session(self, row: dict[str, Any]) -> ParliamentSession:
        rounds_data = json.loads(row["rounds"]) if row.get("rounds") else []
        owl_names_data = json.loads(row["owl_names"]) if row.get("owl_names") else []
        interjections_data = json.loads(row["interjections"]) if row.get("interjections") else []
        rounds = [ParliamentRound(**r) for r in rounds_data]
        started_at = datetime.fromisoformat(row["started_at"])
        completed_at = datetime.fromisoformat(row["completed_at"]) if row.get("completed_at") else None
        return ParliamentSession(
            session_id=row["session_id"],
            topic=row["topic"],
            owl_names=owl_names_data,
            rounds=rounds,
            synthesis=row.get("synthesis"),
            status=row["status"],
            started_at=started_at,
            completed_at=completed_at,
            interjections=interjections_data,
        )
