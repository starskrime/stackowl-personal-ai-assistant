"""MigrationRunner — applies versioned SQL migrations atomically."""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

_BEGIN_RE = re.compile(r"\bBEGIN\b", re.IGNORECASE)
_END_RE = re.compile(r"\bEND\b", re.IGNORECASE)

from stackowl.db.agent_pause import AgentPauseContext
from stackowl.exceptions import MigrationError

log = logging.getLogger("stackowl.db")

_CREATE_SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    version     TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    applied_at  TEXT    NOT NULL,
    checksum    TEXT    NOT NULL
)
"""


def _split_sql(sql: str) -> list[str]:
    """Split SQL into statements, treating BEGIN...END trigger blocks as atomic."""
    statements: list[str] = []
    buf: list[str] = []
    depth = 0
    for seg in sql.split(";"):
        if not seg.strip():
            continue
        buf.append(seg)
        depth += len(_BEGIN_RE.findall(seg)) - len(_END_RE.findall(seg))
        if depth <= 0:
            stmt = ";".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            depth = 0
    if buf:
        stmt = ";".join(buf).strip()
        if stmt:
            statements.append(stmt)
    return statements


@dataclass(frozen=True)
class MigrationResult:
    version: str
    name: str
    action: Literal["applied", "skipped"]


@contextmanager
def _exclusive_tx(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN EXCLUSIVE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception as rb_exc:
            log.warning("[db] exclusive_tx: rollback failed: %s", rb_exc)
        raise


class MigrationRunner:
    """Runs SQL migration files in numeric order, tracking applied versions."""

    def __init__(
        self,
        db_path: Path,
        migrations_dir: Path | None = None,
        agent_pause: AgentPauseContext | None = None,
    ) -> None:
        log.debug("[db] runner.init: entry — db_path=%s", db_path)
        self._db_path = db_path
        self._migrations_dir = migrations_dir or Path(__file__).parent
        self._agent_pause = agent_pause

    def run(self) -> list[MigrationResult]:
        """Apply all pending migrations. Returns one result per migration file."""
        log.debug("[db] runner.run: entry")
        files = self._load_sql_files()
        log.info("[db] runner.run: found %d migration files", len(files))

        if self._agent_pause is not None:
            log.info("[db] runner.run: pausing agents before migration lock")
            self._agent_pause.pause_for_migration()
        try:
            results = self._execute(files)
        finally:
            if self._agent_pause is not None:
                log.info("[db] runner.run: resuming agents after migration")
                self._agent_pause.resume_after_migration()

        applied = sum(1 for r in results if r.action == "applied")
        log.info("[db] runner.run: exit — applied=%d skipped=%d", applied, len(results) - applied)
        return results

    def _execute(self, files: list[tuple[str, str, Path]]) -> list[MigrationResult]:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.isolation_level = None  # manual transaction control
        try:
            conn.execute(_CREATE_SCHEMA_MIGRATIONS)
            results: list[MigrationResult] = []
            for version, name, path in files:
                result = self._apply(conn, version, name, path)
                results.append(result)
            return results
        finally:
            conn.close()

    def _load_sql_files(self) -> list[tuple[str, str, Path]]:
        files: list[tuple[str, str, Path]] = []
        for path in sorted(self._migrations_dir.glob("*.sql")):
            parts = path.stem.split("_", 1)
            if len(parts) < 2:  # noqa: PLR2004
                log.warning("[db] runner: ignoring malformed migration filename %s", path.name)
                continue
            files.append((parts[0], path.name, path))
        return files

    def _apply(self, conn: sqlite3.Connection, version: str, name: str, path: Path) -> MigrationResult:
        row = conn.execute("SELECT version FROM schema_migrations WHERE version = ?", (version,)).fetchone()
        if row is not None:
            log.debug("[db] runner: %s already applied — skipping", name)
            return MigrationResult(version=version, name=name, action="skipped")

        log.info("[db] runner: applying %s", name)
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode()).hexdigest()
        statements = _split_sql(sql)
        try:
            with _exclusive_tx(conn):
                for stmt in statements:
                    conn.execute(stmt)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at, checksum) VALUES (?, ?, ?, ?)",
                    (version, name, datetime.now(tz=UTC).isoformat(), checksum),
                )
                # Advance the stackowl_meta.schema_version pointer inside the SAME
                # exclusive tx so the convenience pointer and the per-migration
                # ledger row commit atomically (no post-loop autocommit lag).
                self._set_schema_version(conn, version)
        except MigrationError:
            raise
        except Exception as exc:
            log.error("[db] runner: %s failed — rolled back", name, exc_info=exc)
            raise MigrationError(name, str(exc)) from exc
        log.info("[db] runner: %s applied successfully", name)
        return MigrationResult(version=version, name=name, action="applied")

    def _set_schema_version(self, conn: sqlite3.Connection, version: str) -> None:
        now = datetime.now(tz=UTC).isoformat()
        conn.execute(
            """INSERT INTO stackowl_meta (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value,
                 updated_at = excluded.updated_at""",
            ("schema_version", version, now),
        )
        log.info("[db] runner: schema_version set to %s", version)
