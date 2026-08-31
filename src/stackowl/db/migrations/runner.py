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

from stackowl.db.agent_pause import AgentPauseContext
from stackowl.exceptions import MigrationError
from stackowl.export.backup import BackupManager
from stackowl.paths import StackowlHome
from stackowl.tools.verification import verify_artifact

# Word-boundary token scanner used by the tokenizer-aware splitter (F020). Only
# the keywords that affect trigger-body bracketing are recognised; everything
# else (including ``end``/``begin`` inside strings/comments) is skipped by the
# tokenizer before these ever match.
_WORD_RE = re.compile(r"[A-Za-z_]+")

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
    """Split SQL into statements, treating ``CREATE TRIGGER … BEGIN … END`` bodies
    as atomic (so the ``;`` between body statements is not a split point).

    A minimal single-pass tokenizer (F020): it walks the text character by
    character, *skipping* the interiors of single/double-quoted strings,
    ``--`` line comments and ``/* … */`` block comments. ``BEGIN``/``END`` are
    counted as trigger-body delimiters ONLY when a ``CREATE TRIGGER`` header was
    seen for the current statement — so an ``END`` in a ``CASE…END`` default, a
    ``begin``/``end`` word inside a string literal or comment, or a bare
    transaction ``BEGIN``/``COMMIT`` never bracket a statement. A statement ends
    at a top-level ``;`` when no trigger body is open.
    """
    statements: list[str] = []
    buf: list[str] = []
    in_trigger = False  # current statement is a CREATE TRIGGER … with a body
    depth = 0  # BEGIN/END nesting inside the open trigger body
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]

        # --- skip comments (their text must not influence tokenizing) ---------
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            j = sql.find("\n", i)
            end = n if j == -1 else j  # comment runs to EOL (newline kept below)
            buf.append(sql[i:end])
            i = end
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            j = sql.find("*/", i + 2)
            end = n if j == -1 else j + 2
            buf.append(sql[i:end])
            i = end
            continue

        # --- skip string / quoted-identifier literals -------------------------
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            while i < n:
                c = sql[i]
                buf.append(c)
                # SQLite escapes a quote by doubling it ('' or "").
                if c == quote:
                    if i + 1 < n and sql[i + 1] == quote:
                        buf.append(sql[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue

        # --- keyword tokens (BEGIN/END/CREATE/TRIGGER) ------------------------
        if ch.isalpha() or ch == "_":
            m = _WORD_RE.match(sql, i)
            assert m is not None  # ch is a word char, so a word matches
            word = m.group(0)
            upper = word.upper()
            buf.append(word)
            if upper == "TRIGGER" and _ends_with_create(buf, word):
                in_trigger = True
            elif in_trigger and upper == "BEGIN":
                depth += 1
            elif in_trigger and upper == "END" and depth > 0:
                depth -= 1
            i = m.end()
            continue

        # --- statement terminator ---------------------------------------------
        if ch == ";":
            buf.append(ch)
            if in_trigger and depth > 0:
                # ``;`` inside an open trigger body — keep going.
                i += 1
                continue
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            in_trigger = False
            depth = 0
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _ends_with_create(buf: list[str], trigger_word: str) -> bool:
    """True if the ``TRIGGER`` token just appended follows ``CREATE`` (allowing
    an optional ``TEMP``/``TEMPORARY`` between them), i.e. this is a real
    ``CREATE [TEMP] TRIGGER`` header and not the bare word ``trigger``."""
    # Reconstruct the preceding word tokens from the buffer (cheap: triggers are
    # rare and buffers are small).
    text = "".join(buf[:-1])  # exclude the just-appended TRIGGER word
    prior_words = _WORD_RE.findall(text)
    if not prior_words:
        return False
    last = prior_words[-1].upper()
    if last == "CREATE":
        return True
    if last in ("TEMP", "TEMPORARY") and len(prior_words) >= 2:  # noqa: PLR2004
        return bool(prior_words[-2].upper() == "CREATE")
    return False


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

    #: How many pre-migration backups survive. Anything that only appends will
    #: poison its reader; each snapshot is a VACUUMed copy of the whole database,
    #: so an unbounded series fills the disk that the next backup needs.
    BACKUPS_RETAINED = 3

    #: Prefix for the directories this runner creates. Retention deletes ONLY
    #: directories matching it — a backup that something else made is never this
    #: code's to remove.
    BACKUP_PREFIX = "pre-migration-"

    def __init__(
        self,
        db_path: Path,
        migrations_dir: Path | None = None,
        agent_pause: AgentPauseContext | None = None,
        backup_root: Path | None = None,
    ) -> None:
        log.debug("[db] runner.init: entry — db_path=%s", db_path)
        self._db_path = db_path
        self._migrations_dir = migrations_dir or Path(__file__).parent
        self._agent_pause = agent_pause
        self._backup_root = backup_root

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
            # A VERIFIED SNAPSHOT BEFORE THE FIRST CHANGE, and only when there is
            # a change to make. _exclusive_tx already prevents a HALF-applied
            # migration; it does nothing about a migration that runs perfectly and
            # deletes the wrong thing, which is what 0112's 242,477 rows were.
            pending = self._pending(conn, files)
            if pending:
                self._backup_before_applying(pending)
            results: list[MigrationResult] = []
            for version, name, path in files:
                result = self._apply(conn, version, name, path)
                results.append(result)
            return results
        finally:
            conn.close()

    def _pending(
        self, conn: sqlite3.Connection, files: list[tuple[str, str, Path]]
    ) -> list[str]:
        """Versions present on disk and absent from ``schema_migrations``."""
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        return [version for version, _name, _path in files if version not in applied]

    def _backup_before_applying(self, pending: list[str]) -> Path:
        """Snapshot the database, OBSERVE the snapshot, prune old ones.

        Fails CLOSED: any failure raises, so nothing is applied. A migration is
        precisely the operation that needs the backup, and an operator who cannot
        write 300MB has a problem worth stopping for. The alternative — apply
        anyway with a warning — is how "we thought we had a backup" happens.
        """
        root = self._backup_root or (StackowlHome.knowledge_dir() / "backups")
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%f")
        dest = root / f"{self.BACKUP_PREFIX}{stamp}"
        log.info(
            "[db] runner.backup: entry — %d pending migration(s), snapshotting to %s",
            len(pending), dest,
        )
        try:
            produced = BackupManager(self._db_path).backup(output_dir=dest)
        except Exception as exc:
            log.error("[db] runner.backup: FAILED — refusing to migrate", exc_info=exc)
            raise RuntimeError(
                f"refusing to apply {len(pending)} migration(s): the pre-migration "
                f"backup could not be taken ({exc}). Free space or fix permissions "
                f"under {root}, then restart."
            ) from exc

        # The returned path is a CLAIM. Observe the file, exactly as every tool
        # that names an artifact already does — this is the 2026-08-30 shape,
        # where an audit row named a backup that was never on disk.
        snapshot = Path(produced) / "stackowl.db"
        if verify_artifact(snapshot) is not True:
            log.error(
                "[db] runner.backup: backup REPORTED success but the file is not there — "
                "refusing to migrate",
                extra={"_fields": {"claimed": str(snapshot)}},
            )
            raise RuntimeError(
                f"refusing to apply {len(pending)} migration(s): the backup claimed "
                f"{snapshot} but no readable, non-empty file is there."
            )

        self._prune_backups(root)
        log.info(
            "[db] runner.backup: exit — verified snapshot at %s (%d bytes)",
            snapshot, snapshot.stat().st_size,
        )
        return Path(produced)

    def _prune_backups(self, root: Path) -> None:
        """Keep the newest ``BACKUPS_RETAINED``; delete only our own directories."""
        import shutil

        try:
            mine = sorted(
                (p for p in root.glob(f"{self.BACKUP_PREFIX}*") if p.is_dir()),
                key=lambda p: p.name,
            )
        except OSError as exc:
            log.warning("[db] runner.backup: could not list %s to prune", root, exc_info=exc)
            return
        for old in mine[: max(0, len(mine) - self.BACKUPS_RETAINED)]:
            try:
                shutil.rmtree(old)
                log.info("[db] runner.backup: pruned old snapshot %s", old)
            except OSError as exc:
                # Never fail a migration because an OLD backup would not delete.
                log.warning("[db] runner.backup: could not prune %s", old, exc_info=exc)

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
